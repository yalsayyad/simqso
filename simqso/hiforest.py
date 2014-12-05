#!/usr/bin/env python

import os
import numpy as np
import scipy.stats as stats
import scipy.constants as const
from scipy.special import gamma as special_gamma
from scipy.interpolate import interp1d
from scipy.integrate import quad
from astropy.io import fits

# shorthands
pi,exp,sqrt = np.pi,np.exp,np.sqrt
c = const.c # m/s
c_kms = c/1e3
c_cms = c*1e2
sqrt_pi = sqrt(pi)
sigma_c = 6.33e-18 # cm^-2
fourpi = 4*pi

def _getlinelistdata():
	# Line list obtained from Prochaska's XIDL code
	# https://svn.ucolick.org/xidl/trunk/Spec/Lines/all_lin.fits
	datadir = os.path.split(__file__)[0]+'/data/'
	linelist = fits.getdata(datadir+'all_lin.fits')
	Hlines = np.array([i for i in range(linelist.size) 
	                       if 'HI' in linelist.ION[i]])
	LySeries = {}
	for n in range(Hlines.size):
		LySeries[n+2] = Hlines[-1-n]
	return linelist,LySeries

linelist,LymanSeries = _getlinelistdata()

# default is to go up to 32->1
default_lymanseries_range = (2,33)

Fan99_model = {
  'forest':{'zrange':(0.0,6.0),
            'logNHrange':(13.0,17.3),
            'N0':50.3,
            'gamma':2.3,
            'beta':1.41,
            'b':30.0},
     'LLS':{'zrange':(0.0,6.0),
            'logNHrange':(17.3,20.5),
            'N0':0.27,
            'gamma':1.55,
            'beta':1.25,
            'b':70.0},
     'DLA':{'zrange':(0.0,6.0),
            'logNHrange':(20.5,22.0),
            'N0':0.04,
            'gamma':1.3,
            'beta':1.48,
            'b':70.0},
}

WP11_model = {
 'forest0':{'zrange':(0.0,1.5),
            'logNHrange':(12.0,19.0),
            'gamma':0.2,
            'beta':1.55,
            'B':0.0170,
            'N0':340.,
            'brange':(10.,100.),
            'bsig':24.0},
 'forest1':{'zrange':(1.5,4.6),
            'logNHrange':(12.0,14.5),
            'gamma':2.04,
            'beta':1.50,
            'B':0.0062,
            'N0':102.0,
            'brange':(10.,100.),
            'bsig':24.0},
 'forest2':{'zrange':(1.5,4.6),
            'logNHrange':(14.5,17.5),
            'gamma':2.04,
            'beta':1.80,
            'B':0.0062,
            'N0':4.05,
            'brange':(10.,100.),
            'bsig':24.0},
 'forest3':{'zrange':(1.5,4.6),
            'logNHrange':(17.5,19.0),
            'gamma':2.04,
            'beta':0.90,
            'B':0.0062,
            'N0':0.051,
            'brange':(10.,100.),
            'bsig':24.0},
    'SLLS':{'zrange':(0.0,4.6),
            'logNHrange':(19.0,20.3),
            'N0':0.0660,
            'gamma':1.70,
            'beta':1.40,
            'brange':(10.,100.),
            'bsig':24.0},
     'DLA':{'zrange':(0.0,4.6),
            'logNHrange':(20.3,22.0),
            'N0':0.0440,
            'gamma':1.27,
            'beta':2.00,
            'brange':(10.,100.),
            'bsig':24.0},
}

forestModels = {'Fan1999':Fan99_model,
                'Worseck&Prochaska2011':WP11_model}

def generateLOS(model,zmin,zmax):
	'''Given a model for the distribution of absorption systems, generate
	   a random line-of-sight populated with absorbers.
	   returns (z,logNHI,b) for each absorption system.
	'''
	abs_dtype = [('z',np.float32),('logNHI',np.float32),('b',np.float32)]
	absorbers = []
	for component,p in model.items():
		if zmin > p['zrange'][1] or zmax < p['zrange'][0]:
			# outside the redshift range of this forest component
			continue
		# parameters for the forest component (LLS, etc.) absorber distribution
		NHImin,NHImax = p['logNHrange']
		NHImin,NHImax = 10**NHImin,10**NHImax
		z1 = max(zmin,p['zrange'][0])
		z2 = min(zmax,p['zrange'][1])
		beta = p['beta'] 
		mbeta1 = -beta+1
		gamma1 = p['gamma'] + 1
		# expectation for the number of absorbers at this redshift
		#  (inverting n(z) = N0*(1+z)^gamma)
		N = (p['N0']/gamma1) * ( (1+z2)**gamma1 - (1+z1)**gamma1 )
		# sample from a Poisson distribution for <N>
		n = stats.poisson.rvs(N,size=1)[0]
		# invert the dN/dz CDF to get the sample redshifts
		x = np.random.random_sample(n)
		z = (1+z1)*((((1+z2)/(1+z1))**gamma1 - 1)*x + 1)**(1/gamma1) - 1
		# invert the NHI CDF to get the sample column densities
		x = np.random.random_sample(n)
		NHI = NHImin*(1 + x*((NHImax/NHImin)**mbeta1 - 1))**(1/mbeta1)
		#
		try: 
			# fixed b
			b = np.array([p['b']]*n,dtype=np.float32)
		except KeyError:
			# dn/db ~ b^-5 exp(-(b/bsig)^-4) (Hui & Rutledge 1999)
			bsig = p['bsig']
			bmin,bmax = p['brange']
			bexp = lambda b: exp(-(b/bsig)**-4)
			x = np.random.random_sample(n)
			b = bsig*(-np.log((bexp(bmax)-bexp(bmin))*x + bexp(bmin)))**(-1./4)
		#
		abs = np.empty(n,dtype=abs_dtype)
		abs['z'] = z
		abs['logNHI'] = np.log10(NHI)
		abs['b'] = b
		absorbers.append(abs)
	return np.concatenate(absorbers)

def voigt(a,x):
	'''Tepper-Garcia 2006, footnote 4 (see erratum)'''
	x2 = x**2
	Q = 1.5/x2
	H0 = exp(-x2)
	return H0 - (a/sqrt_pi)/x2 * (H0*H0*(4*x2*x2 + 7*x2 + 4 + Q) - Q - 1)

class VoigtFast(object):
	def __init__(self,u_range=10.,npts=1000):
		self.logabins = np.array([-3.0 - 0.05*n**1.4 for n in range(30)])
		xfast = np.linspace(-u_range,u_range,npts)
		self.voigt_interp = [interp1d(xfast,voigt(10**loga,xfast),
		                              bounds_error=False,fill_value=0.0)
		                      for loga in self.logabins]
	def __call__(self,a,x):
		ai = np.argmin(np.abs(np.log10(a)-self.logabins))
		return self.voigt_interp[ai](x)

def calc_tau_lambda(los,zem,wave,**kwargs):
	lymanseries_range = kwargs.get('lymanseries_range',
	                               default_lymanseries_range)
	tauMax = kwargs.get('tauMax',15.0)
	tauMin = kwargs.get('tauMin',1e-5)
	tau_lam = kwargs.get('tauIn',np.zeros_like(wave))
	fast = kwargs.get('fast',True)
	if fast:
		# cached Voigt profile using single a value
		voigt_interp = VoigtFast()
		voigt_profile = lambda a,x: voigt_interp(a,x)
	else:
		# full Voigt profile calculation
		voigt_profile = lambda a,x: voigt(a,x)
	#
	ii = np.argsort(los['logNHI'])[::-1]
	NHI = 10**los['logNHI'][ii]
	z1 = 1 + los['z'][ii]
	b = los['b'][ii]
	nabs = len(los)
	# some constants used in tau calculation
	tau_c_lim = sigma_c*NHI
	bnorm = b/c_kms
	# loop over Lyman series transitions, starting at 2->1
	for transition in range(*lymanseries_range):
		# transition properties
		lambda0 = linelist.WREST[LymanSeries[transition]]
		F = linelist.F[LymanSeries[transition]]
		Gamma = linelist.GAMMA[LymanSeries[transition]]
		# Doppler width
		nu_D = b / (lambda0*1e-13)
		# Voigt a parameter
		a = Gamma / (fourpi*nu_D)
		# wavelength of transition at absorber redshift
		lambda_z = lambda0*z1
		# all the values used to calculate tau, now just needs line profile
		c_voigt = 0.014971475 * NHI * F / nu_D
		umax = np.clip(np.sqrt(c_voigt * (a/sqrt_pi)/tauMin),5.0,np.inf)
		for i in xrange(nabs):
			u = (wave/lambda_z[i] - 1) / bnorm[i]
			if umax[i] < u[0]:
				continue
			i1,i2 = np.searchsorted(u,[-umax[i],umax[i]])
			if np.all(tau_lam[i1:i2] > tauMax):
				continue
			u = np.abs(u[i1:i2])
			tau_lam[i1:i2] += c_voigt[i] * voigt_profile(a[i],u)
	return tau_lam
