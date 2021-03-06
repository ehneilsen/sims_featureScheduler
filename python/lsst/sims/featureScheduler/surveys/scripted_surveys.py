import numpy as np
from lsst.sims.featureScheduler.utils import (empty_observation, set_default_nside)
import lsst.sims.featureScheduler.features as features
from lsst.sims.featureScheduler.surveys import BaseSurvey
from lsst.sims.utils import _approx_RaDec2AltAz, _raDec2Hpid
import logging

log = logging.getLogger(__name__)

__all__ = ['Scripted_survey', 'Pairs_survey_scripted']


class Scripted_survey(BaseSurvey):
    """
    Take a set of scheduled observations and serve them up.
    """
    def __init__(self, basis_functions, reward=1e6, ignore_obs='dummy',
                 nside=None, min_alt=30., max_alt=85.):
        """
        min_alt : float (30.)
            The minimum altitude to attempt to chace a pair to (degrees). Default of 30 = airmass of 2.
        max_alt : float(85.)
            The maximum altitude to attempt to chase a pair to (degrees).

        """
        if nside is None:
            nside = set_default_nside()

        self.extra_features = {}

        self.min_alt = np.radians(min_alt)
        self.max_alt = np.radians(max_alt)
        self.nside = nside
        self.reward_val = reward
        self.reward = -reward
        super(Scripted_survey, self).__init__(basis_functions=basis_functions,
                                              ignore_obs=ignore_obs, nside=nside)

    def add_observation(self, observation, indx=None, **kwargs):
        """Check if this matches a scripted observation
        """
        # From base class
        if self.ignore_obs not in observation['note']:
            for feature in self.extra_features:
                self.extra_features[feature].add_observation(observation, **kwargs)
            self.reward_checked = False

            dt = self.obs_wanted['mjd'] - observation['mjd']
            # was it taken in the right time window, and hasn't already been marked as observed.
            time_matches = np.where((np.abs(dt) < self.mjd_tol) & (~self.obs_log))[0]
            for match in time_matches:
                # Might need to change this to an angular distance calc and add another tolerance?
                if (self.obs_wanted[match]['RA'] == observation['RA']) & \
                   (self.obs_wanted[match]['dec'] == observation['dec']) & \
                   (self.obs_wanted[match]['filter'] == observation['filter']):
                    self.obs_log[match] = True
                    break

    def calc_reward_function(self, conditions):
        """If there is an observation ready to go, execute it, otherwise, -inf
        """
        observation = self._check_list()
        if observation is None:
            self.reward = -np.inf
        else:
            self.reward = self.reward_val
        return self.reward

    def _slice2obs(self, obs_row):
        """take a slice and return a full observation object
        """
        observation = empty_observation()
        for key in ['RA', 'dec', 'filter', 'exptime', 'nexp', 'note', 'field_id']:
            observation[key] = obs_row[key]
        return observation

    def _check_alts(self, indices):
        """Check the altitudes of potential matches.
        """
        # This is kind of a kludgy low-resolution way to convert ra,dec to alt,az, but should be really fast.
        # XXX--should I stick the healpixel value on when I set the script? Might be faster.
        # XXX not sure this really needs to be it's own method
        hp_ids = _raDec2Hpid(self.nside, self.obs_wanted[indices]['RA'], self.obs_wanted[indices]['dec'])
        alts = self.extra_features['altaz'].feature['alt'][hp_ids]
        in_range = np.where((alts < self.max_alt) & (alts > self.min_alt))
        indices = indices[in_range]
        return indices

    def _check_list(self, conditions):
        """Check to see if the current mjd is good
        """
        dt = self.obs_wanted['mjd'] - conditions.mjd
        # Check for matches with the right requested MJD
        matches = np.where((np.abs(dt) < self.mjd_tol) & (~self.obs_log))[0]
        # Trim down to ones that are in the altitude limits
        matches = self._check_alts(matches)
        if matches.size > 0:
            observation = self._slice2obs(self.obs_wanted[matches[0]])
        else:
            observation = None
        return observation

    def set_script(self, obs_wanted, mjd_tol=15.):
        """
        Parameters
        ----------
        obs_wanted : np.array
            The observations that should be executed. Needs to have columns with dtype names:
            XXX
        mjds : np.array
            The MJDs for the observaitons, should be same length as obs_list
        mjd_tol : float (15.)
            The tolerance to consider an observation as still good to observe (min)
        """
        self.mjd_tol = mjd_tol/60./24.  # to days
        self.obs_wanted = obs_wanted
        # Set something to record when things have been observed
        self.obs_log = np.zeros(obs_wanted.size, dtype=bool)

    def add_to_script(self, observation, mjd_tol=15.):
        """
        Parameters
        ----------
        observation : observation object
            The observation one would like to add to the scripted surveys
        mjd_tol : float (15.)
            The time tolerance on the observation (minutes)
        """
        self.mjd_tol = mjd_tol/60./24.  # to days
        self.obs_wanted = np.concatenate((self.obs_wanted, observation))
        self.obs_log = np.concatenate((self.obs_log, np.zeros(1, dtype=bool)))
        # XXX--could do a sort on mjd here if I thought that was a good idea.
        # XXX-note, there's currently nothing that flushes this, so adding
        # observations can pile up nonstop. Should prob flush nightly or something

    def generate_observations(self, conditions):
        observation = self._check_list(conditions)
        return [observation]


class Pairs_survey_scripted(Scripted_survey):
    """Check if incoming observations will need a pair in 30 minutes. If so, add to the queue
    """
    def __init__(self, basis_functions, filt_to_pair='griz',
                 dt=40., ttol=10., reward_val=101., note='scripted', ignore_obs='ack',
                 min_alt=30., max_alt=85., lat=-30.2444, moon_distance=30., max_slew_to_pair=15.,
                 nside=None):
        """
        Parameters
        ----------
        filt_to_pair : str (griz)
            Which filters to try and get pairs of
        dt : float (40.)
            The ideal gap between pairs (minutes)
        ttol : float (10.)
            The time tolerance when gathering a pair (minutes)
        """
        if nside is None:
            nside = set_default_nside()

        super(Pairs_survey_scripted, self).__init__(basis_functions=basis_functions,
                                                    ignore_obs=ignore_obs, min_alt=min_alt,
                                                    max_alt=max_alt, nside=nside)

        self.lat = np.radians(lat)
        self.note = note
        self.ttol = ttol/60./24.
        self.dt = dt/60./24.  # To days
        self.max_slew_to_pair = max_slew_to_pair  # in seconds
        self._moon_distance = np.radians(moon_distance)

        self.extra_features = {}
        self.extra_features['Pair_map'] = features.Pair_in_night(filtername=filt_to_pair)

        self.reward_val = reward_val
        self.filt_to_pair = filt_to_pair
        # list to hold observations
        self.observing_queue = []
        # make ignore_obs a list
        if type(self.ignore_obs) is str:
            self.ignore_obs = [self.ignore_obs]

    def add_observation(self, observation, indx=None, **kwargs):
        """Add an observed observation
        """
        # self.ignore_obs not in str(observation['note'])
        to_ignore = np.any([ignore in str(observation['note']) for ignore in self.ignore_obs])
        log.debug('[Pairs.add_observation]: %s: %s: %s', to_ignore, str(observation['note']), self.ignore_obs)
        log.debug('[Pairs.add_observation.queue]: %s', self.observing_queue)
        if not to_ignore:
            # Update my extra features:
            for feature in self.extra_features:
                if hasattr(self.extra_features[feature], 'add_observation'):
                    self.extra_features[feature].add_observation(observation, indx=indx)
            self.reward_checked = False

            # Check if this observation needs a pair
            # XXX--only supporting single pairs now. Just start up another scripted survey
            # to grab triples, etc? Or add two observations to queue at a time?
            # keys_to_copy = ['RA', 'dec', 'filter', 'exptime', 'nexp']
            if ((observation['filter'][0] in self.filt_to_pair) and
                    (np.max(self.extra_features['Pair_map'].feature[indx]) < 1)):
                obs_to_queue = empty_observation()
                for key in observation.dtype.names:
                    obs_to_queue[key] = observation[key]
                # Fill in the ideal time we would like this observed
                log.debug('Observation MJD: %.4f (dt=%.4f)', obs_to_queue['mjd'], self.dt)
                obs_to_queue['mjd'] += self.dt
                self.observing_queue.append(obs_to_queue)
        log.debug('[Pairs.add_observation.queue.size]: %i', len(self.observing_queue))
        for obs in self.observing_queue:
            log.debug('[Pairs.add_observation.queue]: %s', obs)

    def _purge_queue(self, conditions):
        """Remove any pair where it's too late to observe it
        """
        # Assuming self.observing_queue is sorted by MJD.
        if len(self.observing_queue) > 0:
            stale = True
            in_window = np.abs(self.observing_queue[0]['mjd']-conditions.mjd) < self.ttol
            log.debug('Purging queue')
            while stale:
                # If the next observation in queue is past the window, drop it
                if (self.observing_queue[0]['mjd'] < conditions.mjd) & (~in_window):
                    log.debug('Past the window: obs_mjd=%.4f (current_mjd=%.4f)',
                              self.observing_queue[0]['mjd'],
                              conditions.mjd)
                    del self.observing_queue[0]
                # If we are in the window, but masked, drop it
                elif (in_window) & (~self._check_mask(self.observing_queue[0], conditions)):
                    log.debug('Masked')
                    del self.observing_queue[0]
                # If in time window, but in alt exclusion zone
                elif (in_window) & (~self._check_alts(self.observing_queue[0], conditions)):
                    log.debug('in alt exclusion zone')
                    del self.observing_queue[0]
                else:
                    stale = False
                # If we have deleted everything, break out of where
                if len(self.observing_queue) == 0:
                    stale = False

    def _check_alts(self, observation, conditions):
        result = False
        # Just do a fast ra,dec to alt,az conversion. Can use LMST from a feature.

        alt, az = _approx_RaDec2AltAz(observation['RA'], observation['dec'],
                                      self.lat, None,
                                      conditions.mjd,
                                      lmst=conditions.lmst)
        in_range = np.where((alt < self.max_alt) & (alt > self.min_alt))[0]
        if np.size(in_range) > 0:
            result = True
        return result

    def _check_mask(self, observation, conditions):
        """Check that the proposed observation is not currently masked for some reason on the sky map.
        True if the observation is good to observe
        False if the proposed observation is masked
        """

        hpid = np.max(_raDec2Hpid(self.nside, observation['RA'], observation['dec']))
        skyval = conditions.M5Depth[observation['filter'][0]][hpid]

        if skyval > 0:
            return True
        else:
            return False

    def calc_reward_function(self, conditions):
        self._purge_queue(conditions)
        result = -np.inf
        self.reward = result
        log.debug('Pair - calc_reward_func')
        for indx in range(len(self.observing_queue)):

            check = self._check_observation(self.observing_queue[indx], conditions)
            log.debug('%s: %s', check, self.observing_queue[indx])
            if check[0]:
                result = self.reward_val
                self.reward = self.reward_val
                break
            elif not check[1]:
                break

        self.reward_checked = True
        return result

    def _check_observation(self, observation, conditions):

        delta_t = observation['mjd'] - conditions.mjd
        log.debug('Check_observation: obs_mjd=%.4f (current_mjd=%.4f, delta=%.4f, tol=%.4f)',
                  observation['mjd'],
                  conditions.mjd,
                  delta_t,
                  self.ttol)
        obs_hp = _raDec2Hpid(self.nside, observation['RA'], observation['dec'])
        slewtime = conditions.slewtime[obs_hp[0]]
        in_slew_window = slewtime <= self.max_slew_to_pair or delta_t < 0.
        in_time_window = np.abs(delta_t) < self.ttol

        if conditions.current_filter is None:
            infilt = True
        else:
            infilt = conditions.current_filter in self.filt_to_pair

        is_observable = self._check_mask(observation, conditions)
        valid = in_time_window & infilt & in_slew_window & is_observable
        log.debug('Pair - observation: %s ' % observation)
        log.debug('Pair - check[%s]: in_time_window[%s] infilt[%s] in_slew_window[%s] is_observable[%s]' %
                  (valid, in_time_window, infilt, in_slew_window, is_observable))

        return (valid,
                in_time_window,
                infilt,
                in_slew_window,
                is_observable)

    def generate_observations(self, conditions):
        # Toss anything in the queue that is too old to pair up:
        self._purge_queue(conditions)
        # Check for something I want a pair of
        result = []
        # if len(self.observing_queue) > 0:
        log.debug('Pair - call')
        for indx in range(len(self.observing_queue)):

            check = self._check_observation(self.observing_queue[indx], conditions)

            if check[0]:
                result = self.observing_queue.pop(indx)
                result['note'] = 'pair(%s)' % self.note
                # Make sure we don't change filter if we don't have to.
                if conditions.current_filter is not None:
                    result['filter'] = conditions.current_filter
                # Make sure it is observable!
                # if self._check_mask(result):
                result = [result]
                break
            elif not check[1]:
                # If this is not in time window and queue is chronological, none will be...
                break

        return result
