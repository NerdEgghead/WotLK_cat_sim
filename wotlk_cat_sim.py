"""Code for simulating the classic WoW feral cat DPS rotation."""

import numpy as np
import copy
import collections
import urllib
import multiprocessing
import psutil
import sim_utils
import player as player_class
import time


class ArmorDebuffs():

    """Controls the delayed application of boss armor debuffs after an
    encounter begins. At present, only Sunder Armor and Expose Armor are
    modeled with delayed application, and all other boss debuffs are modeled
    as applying instantly at the fight start."""

    def __init__(self, sim):
        """Initialize controller by specifying whether Sunder, EA, or both will
        be applied.

        sim (Simulation): Simulation object controlling fight execution. The
            params dictionary of the Simulation will be modified by the debuff
            controller during the fight.
        """
        self.params = sim.params
        self.process_params()

    def process_params(self):
        """Use the simulation's existing params dictionary to determine whether
        Sunder, EA, or both should be applied."""
        self.use_sunder = bool(self.params['sunder'])
        self.reset()

    def reset(self):
        """Remove all armor debuffs at the start of a fight."""
        self.params['sunder'] = 0

    def update(self, time, player, sim):
        """Add Sunder or EA applications at the appropriate times. Currently,
        the debuff schedule is hard coded as 1 Sunder stack every GCD, and
        EA applied at 15 seconds if used. This can be made more flexible if
        desired in the future using class attributes.

        Arguments:
            time (float): Simulation time, in seconds.
            player (player.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        # If we are Sundering and are at less than 5 stacks, then add a stack
        # every GCD.
        if (self.use_sunder and (self.params['sunder'] < 5)
                and (time >= 1.5 * self.params['sunder'])):
            self.params['sunder'] += 1

            if sim.log:
                sim.combat_log.append(
                    sim.gen_log(time, 'Sunder Armor', 'applied')
                )

            player.calc_damage_params(**self.params)

        return 0.0


class UptimeTracker():

    """Provides an interface for tracking average uptime on buffs and debuffs,
    analogous to Trinket objects."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.uptime = 0.0
        self.last_update = 15.0
        self.active = False
        self.num_procs = 0

    def update(self, time, player, sim):
        """Update average aura uptime at a new timestep.

        Arguments:
            time (float): Simulation time, in seconds.
            player (player.Player): Player object responsible for
                ability casts.
            sim (wotlk_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        if (time > self.last_update) and (time < sim.fight_length - 15):
            dt = time - self.last_update
            active_now = self.is_active(player, sim)
            self.uptime = (
                (self.uptime * (self.last_update - 15.) + dt * active_now)
                / (time - 15.)
            )
            self.last_update = time

            if active_now and (not self.active):
                self.num_procs += 1

            self.active = active_now

        return 0.0

    def is_active(self, player, sim):
        """Determine whether or not the tracked aura is active at the current
        time. This method must be implemented by UptimeTracker subclasses.

        Arguments:
            player (wotlk_cat_sim.Player): Player object responsible for
                ability casts.
            sim (wotlk_cat_sim.Simulation): Simulation object controlling the
                fight execution.

        Returns:
            is_active (bool): Whether or not the aura is currently active.
        """
        return NotImplementedError(
            'Logic for aura active status must be implemented by UptimeTracker'
            ' subclasses.'
        )

    def deactivate(self, *args, **kwargs):
        self.active = False


class RipTracker(UptimeTracker):
    proc_name = 'Rip'

    def is_active(self, player, sim):
        return sim.rip_debuff


class RoarTracker(UptimeTracker):
    proc_name = 'Savage Roar'

    def is_active(self, player, sim):
        return (player.savage_roar or (not player.cat_form))


class Simulation():

    """Sets up and runs a simulated fight with the cat DPS rotation."""

    # Default fight parameters, including boss armor and all relevant debuffs.
    default_params = {
        'gift_of_arthas': True,
        'boss_armor': 3731,
        'sunder': False,
        'faerie_fire': True,
        'blood_frenzy': False,
        'shattering_throw': False,
        'curse_of_elements': True,
    }

    # Default parameters specifying the player execution strategy
    default_strategy = {
        'min_combos_for_rip': 5,
        'use_rake': False,
        'use_bite': True,
        'bite_time': 8.0,
        'min_combos_for_bite': 5,
        'mangle_spam': False,
        'bear_mangle': False,
        'use_berserk': False,
        'prepop_berserk': False,
        'preproc_omen': False,
        'bearweave': False,
        'berserk_bite_thresh': 100,
        'berserk_ff_thresh': 87,
        'lacerate_prio': False,
        'lacerate_time': 10.0,
        'powerbear': False,
        'min_roar_offset': 10.0,
        'roar_clip_leeway': 0.0,
        'snek': False,
        'idol_swap': False,
        'flowershift': False,
        'daggerweave': False,
        'dagger_ep_loss': 1461,
        'mangle_idol_swap': False,
        'max_ff_delay': 1.0,
        'num_targets': 1,
        'aoe': False,
    }

    def __init__(
        self, player, fight_length, latency, trinkets=[], haste_multiplier=1.0,
        hot_uptime=0.0, mangle_idol=None, rake_idol=None, mutilation_idol=None, **kwargs
    ):
        """Initialize simulation.

        Arguments:
            player (Player): An instantiated Player object which can execute
                the DPS rotation.
            fight_length (float): Fight length in seconds.
            latency (float): Modeled player input delay in seconds. Used to
                simulate realistic delays between energy gains and subsequent
                special ability casts, as well as delays in powershift timing
                relative to the GCD.
            trinkets (list of trinkets.Trinket): List of ActivatedTrinket or
                ProcTrinket objects that will be used on cooldown.
            haste_multiplier (float): Total multiplier from external percentage
                haste buffs such as Windfury Totem. Defaults to 1.
            hot_uptime (float): Fractional uptime of Rejuvenation / Wild Growth
                HoTs from a Restoration Druid. Used for simulating Revitalize
                procs. Defaults to 0.
            mangle_idol (trinkets.ProcTrinket): Optional Mangle proc Idol to
                use. If supplied, then Mangle Idol swaps will be configured
                automatically if appropriate.
            rake_idol (trinkets.ProcTrinket): Optional Rake/Lacerate proc Idol
                to use.
            mutilation_idol (trinkets.RefreshingProcTrinket): Optional Mangle/
                Shred proc idol to use.
            kwargs (dict): Key, value pairs for all other encounter parameters,
                including boss armor, relevant debuffs, and player stregy
                specification. An error will be thrown if the parameter is not
                recognized. Any parameters not supplied will be set to default
                values.
        """
        self.player = player
        self.fight_length = fight_length
        self.latency = latency
        self.trinkets = trinkets
        self.mangle_idol = mangle_idol
        self.rake_idol = rake_idol
        self.mutilation_idol = mutilation_idol
        self.params = copy.deepcopy(self.default_params)
        self.strategy = copy.deepcopy(self.default_strategy)

        for key, value in kwargs.items():
            if key in self.params:
                self.params[key] = value
            elif key in self.strategy:
                self.strategy[key] = value
            else:
                raise KeyError(
                    ('"%s" is not a supported parameter. Supported encounter '
                     'parameters are: %s. Supported strategy parameters are: '
                     '%s.') % (key, self.params.keys(), self.strategy.keys())
                )

        # Set up controller for delayed armor debuffs. The controller can be
        # treated identically to a Trinket object as far as the sim is
        # concerned.
        self.debuff_controller = ArmorDebuffs(self)
        self.trinkets.append(self.debuff_controller)

        # Set up trackers for Rip and Roar uptime
        self.trinkets.append(RipTracker())
        self.trinkets.append(RoarTracker())

        # Enable AoE rotation for 3+ targets
        self.strategy['aoe'] = (self.strategy['num_targets'] >= 3)

        # Automatically detect an Idol swapping configuration
        self.shred_bonus = self.player.shred_bonus
        self.rip_bonus = self.player.rip_bonus

        if (self.player.shred_bonus > 0) and (self.player.rip_bonus > 0):
            self.strategy['idol_swap'] = True

        if (self.mangle_idol and (self.shred_bonus or self.rip_bonus)
                and (not self.strategy['aoe'])):
            self.strategy['mangle_idol_swap'] = True

        # Calculate damage ranges for player abilities under the given
        # encounter parameters.
        self.player.calc_damage_params(**self.params)

        # Set multiplicative haste buffs. The multiplier can be increased
        # during Bloodlust, etc.
        self.haste_multiplier = haste_multiplier

        # Calculate time interval between Revitalize dice rolls
        self.revitalize_frequency = 15. / (8 * max(hot_uptime, 1e-9))

    def set_active_debuffs(self, debuff_list):
        """Set active debuffs according to a specified list.

        Arguments:
            debuff_list (list): List of strings containing supported debuff
                names.
        """
        active_debuffs = copy.copy(debuff_list)
        all_debuffs = [key for key in self.params if key != 'boss_armor']

        for key in all_debuffs:
            if key in active_debuffs:
                self.params[key] = True
                active_debuffs.remove(key)
            else:
                self.params[key] = False

        if active_debuffs:
            raise ValueError(
                'Unsupported debuffs found: %s. Supported debuffs are: %s.' % (
                    active_debuffs, self.params.keys()
                )
            )

        self.debuff_controller.process_params()

    def gen_log(self, time, event, outcome):
        """Generate a custom combat log entry.

        Arguments:
            time (float): Current simulation time in seconds.
            event (str): First "event" field for the log entry.
            outcome (str): Second "outcome" field for the log entry.
        """
        return [
            '%.3f' % time, event, outcome, '%.1f' % self.player.energy,
            '%d' % self.player.combo_points, '%d' % self.player.mana,
            '%d' % self.player.rage
        ]

    def mangle(self, time):
        """Instruct the Player to Mangle, and perform related bookkeeping.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            damage_done (float): Damage done by the Mangle cast.
        """
        damage_done, success = self.player.mangle()

        # If it landed, flag the debuff as active and start timer
        if success:
            self.mangle_debuff = True
            self.mangle_end = (
                np.inf if self.strategy['bear_mangle'] else (time + 60.0)
            )

        # If Idol swapping is configured, then swap to Shred or Rip Idol
        # immmediately after Mangle is cast. This incurs a 0.5 second GCD
        # extension as well as a swing timer reset, so it should only be done
        # in Cat Form.
        if (self.strategy['mangle_idol_swap'] and self.player.cat_form
                and self.mangle_idol.equipped):
            self.player.shred_bonus = (
                0 if self.strategy['idol_swap'] else self.shred_bonus
            )
            self.player.rip_bonus = self.rip_bonus
            self.player.calc_damage_params(**self.params)
            self.player.gcd = 1.5
            self.update_swing_times(
                time + self.swing_timer, self.swing_timer, first_swing=True
            )
            self.mangle_idol.equipped = False

        return damage_done

    def rake(self, time):
        """Instruct the Player to Rake, and perform related bookkeeping.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            damage_done (float): Damage done by the Rake initial hit.
        """
        damage_done, success = self.player.rake(self.mangle_debuff)

        # If it landed, flag the debuff as active and start timer
        if success:
            self.rake_debuff = True
            self.rake_end = time + self.player.rake_duration
            self.rake_ticks = list(np.arange(time + 3, self.rake_end + 1e-9, 3))
            self.rake_damage = self.player.rake_tick
            self.rake_crit_chance = self.player.crit_chance
            self.rake_sr_snapshot = self.player.savage_roar

        return damage_done

    def lacerate(self, time):
        """Instruct the Player to Lacerate, and perform related bookkeeping.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            damage_done (float): Damage done by the Lacerate initial hit.
        """
        damage_done, success = self.player.lacerate(self.mangle_debuff)

        if success:
            self.lacerate_end = time + 15.0

            if self.lacerate_debuff:
                # Unlike our other bleeds, Lacerate maintains its tick rate
                # when it is refreshed, so we simply append more ticks to
                # extend the duration. Note that the current implementation
                # allows for Lacerate to be refreshed *after* the final tick
                # goes out as long as it happens before the duration expires.
                if self.lacerate_ticks:
                    last_tick = self.lacerate_ticks[-1]
                else:
                    last_tick = self.last_lacerate_tick

                self.lacerate_ticks += list(np.arange(
                    last_tick + 3, self.lacerate_end + 1e-9, 3
                ))
                self.lacerate_stacks = min(self.lacerate_stacks + 1, 5)
            else:
                self.lacerate_debuff = True
                self.lacerate_ticks = list(np.arange(time + 3, time + 16, 3))
                self.lacerate_stacks = 1

            self.lacerate_damage = (
                self.player.lacerate_tick * self.lacerate_stacks
                * (1 + 0.15 * self.player.enrage) * self.player.lacerate_dot_multi
            )
            self.lacerate_crit_chance = self.player.crit_chance - 0.04

        return damage_done

    def rip(self, time):
        """Instruct Player to apply Rip, and perform related bookkeeping.

        Arguments:
            time (float): Current simulation time in seconds.
        """
        damage_per_tick, success = self.player.rip()

        if success:
            self.rip_debuff = True
            self.rip_start = time
            self.rip_end = time + self.player.rip_duration
            self.rip_ticks = list(np.arange(time + 2, self.rip_end + 1e-9, 2))
            self.rip_damage = damage_per_tick
            self.rip_crit_bonus_chance = self.player.crit_chance + self.player.rip_crit_bonus
            self.rip_sr_snapshot = self.player.savage_roar

        # If Idol swapping is configured, then swap to Shred Idol immmediately
        # after Rip is cast. This incurs a 0.5 second GCD extension as well as
        # a swing timer reset, so it should only be done during Berserk.
        if (self.strategy['idol_swap'] and (self.player.rip_bonus > 0)
                and self.player.berserk):
            self.player.shred_bonus = self.shred_bonus
            self.player.rip_bonus = 0
            self.player.calc_damage_params(**self.params)
            self.player.gcd = 1.5
            self.update_swing_times(
                time + self.swing_timer, self.swing_timer, first_swing=True
            )

        return 0.0

    def shred(self):
        """Instruct Player to Shred, and perform related bookkeeping.

        Returns:
            damage_done (Float): Damage done by Shred cast.
        """
        damage_done, success = self.player.shred(self.mangle_debuff)

        # If it landed, apply Glyph of Shred
        if success and self.rip_debuff and self.player.shred_glyph:
            if (self.rip_end - self.rip_start) < self.player.rip_duration + 6:
                self.rip_end += 2
                self.rip_ticks.append(self.rip_end)

        return damage_done

    def berserk_expected_at(self, current_time, future_time):
        """Determine whether the Berserk buff is predicted to be active at
        the requested future time.

        Arguments:
            current_time (float): Current simulation time in seconds.
            future_time (float): Future time, in seconds, for querying Berserk
                status.

        Returns:
            berserk_expected (bool): True if Berserk should be active at the
                specified future time, False otherwise.
        """
        if self.player.berserk:
            return (
                (future_time < self.berserk_end)
                or (future_time > current_time + self.player.berserk_cd)
            )
        if self.player.berserk_cd > 1e-9:
            return (future_time > current_time + self.player.berserk_cd)
        if self.params['tigers_fury'] and self.strategy['use_berserk']:
            return (future_time > self.tf_end)
        return False

    def tf_expected_before(self, current_time, future_time):
        """Determine whether Tiger's Fury is predicted to be used prior to the
        requested future time.

        Arguments:
            current_time (float): Current simulation time in seconds.
            future_time (float): Future time, in seconds, for querying Tiger's
                Fury status.

        Returns:
            tf_expected (bool): True if Tiger's Fury should be activated prior
                to the specified future time, False otherwise.
        """
        if self.player.tf_cd > 1e-9:
            return (current_time + self.player.tf_cd < future_time)
        if self.player.berserk:
            return (self.berserk_end < future_time)
        return True

    def can_bite(self, time):
        """Determine whether or not there is sufficient time left before Rip
        falls off to fit in a Ferocious Bite. Uses either a fixed empirically
        optimized time parameter or a first principles analytical calculation
        depending on user options.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            can_bite (bool): True if Biting now is optimal.
        """
        if self.strategy['bite_time'] is not None:
            bt = self.strategy['bite_time']
            # max_rip_dur = (
            #     self.player.rip_duration + 6 * self.player.shred_glyph
            # )
            # rip_end = self.rip_start + max_rip_dur

            # if self.rip_end < self.roar_end:
            #     bt -= 6 * self.tf_expected_before(time, self.rip_end)
            # else:
            #     bt -= 6 * self.tf_expected_before(time, self.roar_end)

            return (
                (self.rip_end - time >= bt) and (self.roar_end - time >= bt)
            )
        return self.can_bite_analytical(time)

    def can_bite_analytical(self, time):
        """Analytical alternative to the empirical bite_time parameter used for
        determining whether there is sufficient time left before Rip falls off
        to fit in a Ferocious Bite.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            can_bite (bool): True if the analytical model indicates that Biting
                now is optimal, False otherwise.
        """
        # First calculate how much Energy we expect to accumulate before our
        # next finisher expires.
        maxripdur = self.player.rip_duration + 6 * self.player.shred_glyph
        ripdur = self.rip_start + maxripdur - time
        srdur = self.roar_end - time
        mindur = min(ripdur, srdur)
        maxdur = max(ripdur, srdur)
        expected_energy_gain_min = 10 * mindur
        expected_energy_gain_max = 10 * maxdur

        if self.tf_expected_before(time, time + mindur):
            expected_energy_gain_min += 60
        if self.tf_expected_before(time, time + maxdur):
            expected_energy_gain_max += 60

        if self.player.omen:
            expected_energy_gain_min += mindur / self.swing_timer * (
                3.5 / 60. * (1 - self.player.miss_chance) * 42
            )
            expected_energy_gain_max += maxdur / self.swing_timer * (
                3.5 / 60. * (1 - self.player.miss_chance) * 42
            )

        expected_energy_gain_min += mindur/self.revitalize_frequency*0.15*8
        expected_energy_gain_max += maxdur/self.revitalize_frequency*0.15*8

        total_energy_min = self.player.energy + expected_energy_gain_min
        total_energy_max = self.player.energy + expected_energy_gain_max

        # Now calculate the effective Energy cost for Biting now, which
        # includes the cost of the Ferocious Bite itself, the cost of building
        # CPs for Rip and Roar, and the cost of Rip/Roar.
        ripcost, bitecost, srcost = self.get_finisher_costs(time)
        cost_per_builder = (
            (42. + 42. + 35.) / 3. * (1 + 0.2 * self.player.miss_chance)
        )

        # Aus did an analytical waterfall calculation of the expected number of
        # builders required for building 5 CPs
        cc = self.player.crit_chance
        required_builders_min = cc**4 - 2 * cc**3 + 3 * cc**2 - 4 * cc + 5

        if srdur < ripdur:
            nextcost = srcost
            required_builders_max = 2 * required_builders_min
        else:
            nextcost = ripcost
            required_builders_max = required_builders_min + 1

        total_energy_cost_min = (
            bitecost + required_builders_min * cost_per_builder + nextcost
        )
        total_energy_cost_max = (
            bitecost + required_builders_max * cost_per_builder + ripcost
            + srcost
        )

        # Actual Energy cost is a bit lower than this because it is okay to
        # lose a few seconds of Rip or SR uptime to gain a Bite.
        rip_downtime, sr_downtime = self.calc_allowed_rip_downtime(time)

        # Adjust downtime estimate to account for end of fight losses
        rip_downtime = maxripdur * (1 - 1. / (1. + rip_downtime / maxripdur))
        sr_downtime = 34. * (1 - 1. / (1. + sr_downtime / 34.))
        next_downtime = sr_downtime if srdur < ripdur else rip_downtime

        total_energy_cost_min -= 10 * next_downtime
        total_energy_cost_max -= 10 * min(rip_downtime, sr_downtime)

        # Then we simply recommend Biting now if the available Energy to do so
        # exceeds the effective cost.
        return (
            (total_energy_min > total_energy_cost_min)
            and (total_energy_max > total_energy_cost_max)
        )

    def get_finisher_costs(self, time):
        """Determine the expected Energy cost for Rip when it needs to be
        refreshed, and the expected Energy cost for Ferocious Bite if it is
        cast right now.

        Arguments:
            time (float): Current simulation time, in seconds.

        Returns:
            ripcost (float): Energy cost of future Rip refresh.
            bitecost (float): Energy cost of a current Ferocious Bite cast.
            srcost (float): Energy cost of a Savage Roar refresh.
        """
        rip_end = time if (not self.rip_debuff) else self.rip_end
        ripcost = self.player._rip_cost / 2 if self.berserk_expected_at(time, rip_end) else self.player._rip_cost

        if self.player.energy >= self.player.bite_cost:
            bitecost = min(self.player.bite_cost + 30, self.player.energy)
        else:
            bitecost = self.player.bite_cost + 10 * self.latency

        sr_end = time if (not self.player.savage_roar) else self.roar_end
        srcost = 12.5 if self.berserk_expected_at(time, sr_end) else 25

        return ripcost, bitecost, srcost

    def calc_allowed_rip_downtime(self, time):
        """Determine how many seconds of Rip uptime can be lost in exchange for
        a Ferocious Bite cast without losing damage. This calculation is used
        in the analytical bite_time calculation above, as well as for
        determining how close to the end of the fight we should be for
        prioritizing Bite over Rip.

        Arguments:
            time (float): Current simulation time, in seconds.

        Returns:
            allowed_rip_downtime (float): Maximum acceptable Rip duration loss,
                in seconds.
            allowed_sr_downtime (float): Maximum acceptable Savage Roar
                downtime, in seconds.
        """
        rip_cp = self.strategy['min_combos_for_rip']
        bite_cp = self.strategy['min_combos_for_bite']
        rip_cost, bite_cost, roar_cost = self.get_finisher_costs(time)
        crit_factor = self.player.calc_crit_multiplier() - 1
        bite_base_dmg = 0.5 * (
            self.player.bite_low[bite_cp] + self.player.bite_high[bite_cp]
        )
        bite_bonus_dmg = (
            (bite_cost - self.player.bite_cost)
            * (9.4 + self.player.attack_power / 410.)
            * self.player.bite_multiplier
        )
        bite_dpc = (bite_base_dmg + bite_bonus_dmg) * (
            1 + crit_factor * (self.player.crit_chance + 0.25)
        )
        crit_mod = crit_factor * self.player.crit_chance
        avg_rip_tick = self.player.rip_tick[rip_cp] * 1.3 * (
            1 + crit_mod * self.player.primal_gore
        )
        shred_dpc = (
            0.5 * (self.player.shred_low + self.player.shred_high) * 1.3
            * (1 + crit_mod)
        )
        allowed_rip_downtime = (
            (bite_dpc - (bite_cost - rip_cost) * shred_dpc / 42.)
            / avg_rip_tick * 2
        )
        cpe = (42. * bite_dpc / shred_dpc - 35.) / 5.
        srep = {1: (1 - 5) * (cpe - 125./34.), 2: (2 - 5) * (cpe - 125./34.)}
        srep_avg = (
            self.player.crit_chance * srep[2]
            + (1 - self.player.crit_chance) * srep[1]
        )
        rake_dpc = 1.3 * (
            self.player.rake_hit * (1 + crit_mod)
            + 3*self.player.rake_tick*(1 + crit_mod*self.player.primal_gore)
        )
        allowed_sr_downtime = (
            (bite_dpc - shred_dpc / 42. * min(srep_avg, srep[1], srep[2]))
            / (0.33/1.33 * rake_dpc)
        )
        return allowed_rip_downtime, allowed_sr_downtime

    def calc_builder_dpe(self):
        """Calculate current damage-per-Energy of Rake vs. Shred. Used to
        determine whether Rake is worth casting when player stats change upon a
        dynamic proc occurring.

        Returns:
            rake_dpe (float): Average DPE of a Rake cast with current stats.
            shred_dpe (float): Average DPE of a Shred cast with current stats.
        """
        crit_factor = self.player.calc_crit_multiplier() - 1
        crit_mod = crit_factor * self.player.crit_chance
        shred_dpc = (1 + self.player.roar_fac) * (
            0.5 * (self.player.shred_low + self.player.shred_high) * 1.3
            * (1 + crit_mod)
        )
        rake_dpc = 1.3 * (1 + self.player.roar_fac) * (
            self.player.rake_hit * (1 + crit_mod)
            + (self.player.rake_duration / 3) * self.player.rake_tick * (1 + crit_mod * self.player.t10_4p_bonus)
        )
        return rake_dpc/self.player.rake_cost, shred_dpc/self.player.shred_cost

    def bite_over_rip(self, refresh_time, future_refresh=False):
        """Determine whether Rip will tick enough times between the specified
        cast time and the end of the fight for it to be worth casting over
        Ferocious Bite.

        Arguments:
            refresh_time (float): Time of potential Rip cast, in seconds.
            future_refresh (bool): If True, then refresh_time is interpreted as
                a future point in time rather than right now. In this case, a
                minimum Energy Bite will be assumed, rather than using the
                player's current Energy. Defaults False.

        Returns:
            should_bite (bool): True if Bite will provide higher DPE than Rip
                given the expected number of ticks.
        """
        rip_dpe, bite_dpe = self.calc_spender_dpe(
            refresh_time, future_refresh=future_refresh
        )
        return (bite_dpe > rip_dpe)

    def calc_spender_dpe(self, refresh_time, future_refresh=False):
        """Calculate damage-per-Energy of Rip vs. Ferocious Bite at the
        specified Rip cast time.

        Arguments:
            refresh_time (float): Time of potential Rip cast, in seconds.
            future_refresh (bool): If True, then refresh_time is interpreted as
                a future point in time rather than right now. In this case, a
                minimum Energy Bite will be assumed, rather than using the
                player's current Energy. Defaults False.

        Returns:
            rip_dpe (float): Average DPE of a Rip cast at refresh_time.
            bite_dpe (float): Average DPE of a Bite cast at refresh_time.
        """
        if future_refresh:
            bite_spend = 35
            bite_cost = 35
            bite_cp = self.strategy['min_combos_for_bite']
            rip_cost = self.player._rip_cost
            rip_cp = self.strategy['min_combos_for_rip']
        else:
            bite_cost = 0 if self.player.omen_proc else self.player.bite_cost
            bite_spend = max(
                min(self.player.energy, bite_cost + 30),
                bite_cost + 10 * self.latency
            )
            bite_cp = self.player.combo_points
            rip_cost = self.player.rip_cost
            rip_cp = self.player.combo_points

        # Rip DPE calculation
        max_rip_dur = self.player.rip_duration + 6 * self.player.shred_glyph
        rip_dur = min(max_rip_dur, self.fight_length - refresh_time)
        num_rip_ticks = rip_dur // 2 # floored integer division here
        crit_factor = self.player.calc_crit_multiplier() - 1
        rip_crit_chance = self.player.crit_chance + self.player.rip_crit_bonus
        avg_rip_tick = self.player.rip_tick[rip_cp] * 1.3 * (
            1 + crit_factor * rip_crit_chance * self.player.primal_gore
        )
        rip_dpe = avg_rip_tick * num_rip_ticks / rip_cost

        # Bite DPE calculation
        bite_base_dmg = 0.5 * (
            self.player.bite_low[bite_cp] + self.player.bite_high[bite_cp]
        )
        bite_bonus_dmg = (
            (bite_spend - bite_cost) * (9.4 + self.player.attack_power / 410.)
            * self.player.bite_multiplier
        )
        bite_crit_chance = min(
            1.0, self.player.crit_chance + self.player.bite_crit_bonus
        )
        bite_dpe = (bite_base_dmg + bite_bonus_dmg) / bite_spend * (
            1 + crit_factor * bite_crit_chance
        )

        return rip_dpe, bite_dpe

    def clip_roar(self, time):
        """Determine whether to clip a currently active Savage Roar in order to
        de-sync the Rip and Roar timers.

        Arguments:
            time (float): Current simulation time in seconds.
        Returns:
            can_roar (bool): Whether or not to clip Roar now.
        """
        if (not self.rip_debuff) or self.block_rip_next:
            return False

        # Project Rip end time assuming full Glyph of Shred extensions.
        max_rip_dur = self.player.rip_duration + 6 * self.player.shred_glyph
        rip_end = self.rip_start + max_rip_dur

        # If the existing Roar already falls off well after the existing Rip,
        # then no need to clip.
        if self.roar_end > rip_end + self.strategy['roar_clip_leeway']:
            return False

        # If the existing Roar already covers us to the end of the fight, then
        # no need to clip.
        if self.roar_end >= self.fight_length:
            return False

        # Calculate when Roar would end if we cast it now.
        new_roar_dur = (
            self.player.roar_durations[self.player.combo_points]
            + 8 * self.player.t8_4p_bonus
        )
        new_roar_end = time + new_roar_dur

        # Clip as soon as we have enough CPs for the new Roar to expire well
        # after the current Rip.
        return (new_roar_end >= rip_end + self.strategy['min_roar_offset'])

    def emergency_roar(self, time):
        """This function handles special logic to handle overriding the
        standard Roar offsetting logic above in cases where the Rip and Roar
        timers have become so closely synced relative to available Energy/CPs
        that an inefficient low-CP Roar clip is required to save both timers.
        Specifically, if Rip is currently not applied or is due to fall off
        before the current Roar, then the standard logic forbids a Roar clip
        and instead recommends building for Rip first. But in cases where there
        is simply not enough time to build for Rip before Roar will *also*
        expire, then clipping Roar first should be preferable.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            emergency_roar_now (bool): Whether or not to execute an emergency
                SR cast.
        """
        # The emergency logic does not apply if we are in "normal" offsetting
        # territory where the current Roar will expire before the current Rip,
        # or if we are close enough to end of fight.
        if self.roar_end >= self.fight_length:
            return False
        if (self.rip_debuff and (self.roar_end < self.rip_end)):
            return False
        if self.block_rip_next:
            return False

        # Perform an estimate of the earliest we could reasonably cast Rip
        # given current Energy/CP and FF/TF timers. Assume that all builders
        # will Crit but no natural Omen procs.
        min_builders_for_rip = np.ceil(
            (self.strategy['min_combos_for_rip']-self.player.combo_points)/2
        )
        energy_for_rip = (
            min_builders_for_rip * self.player.shred_cost
            + self.player.rip_cost
        )
        ff_available = self.player.faerie_fire_cd < (self.roar_end - time)
        gcd_time_for_rip = min_builders_for_rip + 1.0 + ff_available
        energy_time_for_rip = 0.1 * (
            energy_for_rip - self.player.energy
            - (ff_available + self.player.omen_proc) * self.player.shred_cost
            - 60. * self.tf_expected_before(time, self.roar_end)
        )
        min_time_for_rip = max(gcd_time_for_rip, energy_time_for_rip)

        # If min_time_for_rip takes us too close to fight end, then don't
        # emergency Roar since we'll just be Biting anyway.
        if self.bite_over_rip(time + min_time_for_rip, future_refresh=True):
            return False

        # Perform the emergency Roar if min_time_for_rip exceeds the remaining
        # Roar duration in order to prevent the disaster scenario where both
        # are down simultaneously.
        return (time + min_time_for_rip >= self.roar_end)

    def should_bearweave(self, time):
        """Determine whether the player should initiate a bearweave.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            can_bearweave (float): Whether or not a a bearweave should be
                initiated at the specified time.
        """
        rip_refresh_pending = self.rip_refresh_pending
        ff_leeway = self.strategy['max_ff_delay']

        # First check basic conditions for any type of bearweave, and return
        # False if these are not met. All weave sequences involve 2 1.5 second
        # shapeshift GCDs (both delayed by latency), 1 Mangle (Bear) cast,
        # 1 Faerie Fire cast (either in cat or bear), and 1 Cat Form GCD to
        # spend the Omen proc, and therefore take 6.5 seconds + 2 * latency to
        # execute.
        weave_end = time + 6.5 + 2 * self.latency
        can_weave = (
            self.strategy['bearweave'] and self.player.cat_form
            and (not self.player.omen_proc) and (not self.player.berserk)
            and ((not rip_refresh_pending) or (self.rip_end >= weave_end))
        )

        if can_weave and (not self.strategy['lacerate_prio']):
            can_weave = not self.tf_expected_before(time, weave_end)

        # Also add an end of fight condition to make sure we can spend down our
        # Energy post-bearweave before the encounter ends. Time to spend is
        # given by weave_end plus 1 second per 42 Energy that we have at
        # weave_end.
        if can_weave:
            energy_to_dump = self.player.energy + (weave_end - time) * 10
            can_weave = (
                weave_end + energy_to_dump // 42 < self.fight_length
            )

        if not can_weave:
            return False

        # Now we check for conditions that allow for initiating one of two
        # "flavors" of bearweaves: either a single-GCD Mangleweave or a two-GCD
        # Mangle + Faerie Fire weave. These have different leeway constraints
        # due to their differing lengths.

        # Start with the simple Mangleweave. Here we FF in Cat Form after
        # exiting the weave, so the maximum Energy cap is 65 when shifting back
        # into cat (15 for Cat Form GCD, 10 for FF GCD, 10 to spend the Omen).
        # The FF cast will happen 4.5 + 2 * latency seconds after initiation.
        mangleweave_furor_cap = min(20 * self.player.furor, 65)
        mangleweave_energy = mangleweave_furor_cap - 30 - 20 * self.latency
        ff_cd = self.player.faerie_fire_cd
        can_mangleweave = (
            (self.player.energy <= mangleweave_energy)
            and (ff_cd >= 4.5 + 2 * self.latency - ff_leeway)
        )

        # Now check the "Manglefire" sequence. Here we FF in Dire Bear Form
        # *before* exiting the weave, so the maximum Energy cap is 75 when
        # shifting back into cat (15 for Cat Form GCD and 10 to spend the Omen
        # proc).The FF cast will nominally happen 3 + latency seconds after
        # initiation, with possible additional small delays to wait for a Maul
        # before casting FF.
        manglefire_furor_cap = min(20 * self.player.furor, 75)
        manglefire_energy = manglefire_furor_cap - 40 - 20 * self.latency
        can_manglefire = (
            (self.player.energy <= manglefire_energy)
            and (ff_cd >= 3.0 + self.latency - ff_leeway)
        )

        return (can_mangleweave or can_manglefire)

    def execute_rotation(self, time):
        """Execute the next player action in the DPS rotation according to the
        specified player strategy in the simulation.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            damage_done (float): Damage done by the player action.
        """
        # If we previously decided to cast GotW, then execute the cast now once
        # the input delay is over.
        if self.player.ready_to_gift:
            self.player.flowershift(time)

            # If daggerweaving, then GotW GCD is reset to 1.5 seconds
            # regardless of Spell Haste.
            if self.strategy['daggerweave']:
                self.player.gcd = 1.5 + self.latency

            # Reset swing timer based on equipped weapon speed
            next_swing = time + max(
                self.swing_timer * self.player.weapon_speed,
                self.player.gcd + 2 * self.latency
            )
            self.update_swing_times(
                next_swing, self.swing_timer, first_swing=True
            )

            return 0.0

        # If we previously decided to shift, then execute the shift now once
        # the input delay is over.
        if self.player.ready_to_shift:
            bear_to_cat = self.player.bear_form
            self.player.shift(time)

            if (self.player.mana < 0) and (not self.time_to_oom):
                self.time_to_oom = time

            # Swing timer only updates on the next swing after we shift
            if bear_to_cat or self.player.bear_form:
                swing_fac = 1./2.5 if self.player.cat_form else 2.5
            else:
                swing_fac = 1.

            new_timer = self.swing_timer * swing_fac
            next_swing = self.swing_times[0]

            # Add support for swing timer resets using Albino Snake summon or
            # duplicate weapon swap or Idol swap.
            rip_refresh_soon = (
                ((not self.rip_debuff) and (self.fight_length - time >= 10))
                or (self.rip_debuff and (self.rip_end - time <= 9) and
                        (self.fight_length - self.rip_end >= 10))
            )
            swap_idols = self.strategy['idol_swap'] and (
                ((self.player.shred_bonus > 0) and rip_refresh_soon)
                or ((self.player.rip_bonus > 0) and (not rip_refresh_soon))
            )

            if self.strategy['mangle_idol_swap']:
                swap_idols = swap_idols or self.mangle_idol.equipped

            if self.player.cat_form and (self.strategy['snek'] or swap_idols):
                next_swing = time + new_timer

            # If we weapon swapped to a fast dagger when casting GotW, then we
            # can perform a weaker auto-attack immediately upon shifting prior
            # to swapping back to our normal weapon.
            if self.strategy['flowershift'] and self.strategy['daggerweave']:
                next_swing = time
                self.player.attack_power -= (
                    self.strategy['dagger_ep_loss'] * self.player.ap_mod
                )
                self.player.calc_damage_params(**self.params)
                self.player.dagger_equipped = True
                self.player.gcd = 1.5 + self.latency

            self.update_swing_times(next_swing, new_timer, first_swing=True)

            # Toggle between Rip and Shred Idols if configured to do so
            if swap_idols and self.player.cat_form:
                if self.mangle_idol and self.mangle_idol.equipped:
                    self.mangle_idol.equipped = False

                    # Hack to re-use swapping logic below
                    if self.strategy['idol_swap']:
                        self.player.shred_bonus = rip_refresh_soon
                    else:
                        self.player.shred_bonus = self.rip_bonus

                if self.player.shred_bonus:
                    self.player.shred_bonus = 0
                    self.player.rip_bonus = self.rip_bonus
                    log_str = 'equip Rip Idol'
                else:
                    self.player.shred_bonus = self.shred_bonus
                    self.player.rip_bonus = 0
                    log_str = 'equip Shred Idol'

                self.player.calc_damage_params(**self.params)
                self.player.gcd = 1.5 + self.latency

                if self.log:
                    self.player.combat_log[1] = log_str

            return 0.0

        energy, cp = self.player.energy, self.player.combo_points
        rip_cp = self.strategy['min_combos_for_rip']
        bite_cp = self.strategy['min_combos_for_bite']

        # block_rip_now prevents Rip usage too close to fight end
        self.block_rip_now = (cp < rip_cp) or self.bite_over_rip(time)
        rip_now = (
            (cp >= rip_cp) and (not self.rip_debuff)
            # and (not self.player.omen_proc)
            and (not self.block_rip_now)
        )

        # Do not spend Clearcasting on Rip *unless* we are in an edge case
        # where Roar is nearly about to expire.
        if rip_now and self.player.omen_proc:
            # Determine the earliest we could cast Rip if we do *not* use our
            # Omen proc on it, and instead spend it on Shred first.
            rip_cast_time = time + max(
                1.0, (self.player.rip_cost - energy) / 10. + self.latency
            )
            rip_now = (
                self.player.savage_roar and (rip_cast_time >= self.roar_end)
            )

        # Likewise, block_rip_next prios Bite usage if the *current* Rip will
        # expire too close to fight end.
        self.block_rip_next = self.rip_debuff and (
            self.bite_over_rip(self.rip_end, future_refresh=True)
        )
        bite_at_end = (
            (cp >= bite_cp) and (self.block_rip_now or self.block_rip_next)
        )

        # Clip Mangle if it won't change the total number of Mangles we have to
        # cast before the fight ends.
        mangle_refresh_now = (
            (not self.mangle_debuff) and (time < self.fight_length - 1.0)
        )
        mangle_refresh_pending = (
            self.mangle_debuff and (self.mangle_end < self.fight_length - 1.0)
        )
        clip_mangle = False

        if mangle_refresh_pending:
            num_mangles_remaining = (
                1 + (self.fight_length - 1.0 - self.mangle_end) // 60
            )
            earliest_mangle = self.fight_length - num_mangles_remaining * 60
            clip_mangle = (time >= earliest_mangle)

        mangle_now = (
            (not rip_now) and (not self.strategy['aoe'])
            and (mangle_refresh_now or clip_mangle)
            # and (not self.player.omen_proc)
        )
        aoe_mangle = (
            self.strategy['aoe'] and (self.mangle_idol or self.mutilation_idol) and (cp == 0) and
            ((not self.player.savage_roar) or (self.roar_end - time <= 1.0))
        )
        mangle_now = mangle_now or aoe_mangle
        mangle_cost = self.player.mangle_cost

        bite_before_rip = (
            (cp >= bite_cp) and self.rip_debuff and self.player.savage_roar
            and self.strategy['use_bite'] and self.can_bite(time)
        )
        bite_now = (bite_before_rip or bite_at_end) and (energy < 67)

        if bite_now and self.player.omen_proc:
            # _, shred_dpe = self.calc_builder_dpe()
            # _, bite_dpe = self.calc_spender_dpe(time)
            # bite_energy_drain = min(energy, 30)
            # effective_bite_cost = (
            #     bite_energy_drain +
            #     (self.player.shred_cost - self.player.bite_cost)
            # )
            # effective_bite_dpe = (
            #     (bite_dpe*bite_energy_drain - shred_dpe*self.player.shred_cost)
            #     / effective_bite_cost
            # )
            # bite_now = (effective_bite_dpe > shred_dpe)
            bite_now = False

        # During Berserk, we additionally add an Energy constraint on Bite
        # usage to maximize the total Energy expenditure we can get.
        if bite_now and self.player.berserk:
            bite_now = (energy <= self.strategy['berserk_bite_thresh'])

        rake_now = (
            (self.strategy['use_rake']) and (not self.rake_debuff)
            and (self.fight_length - time > 9)
            and (not self.player.omen_proc)
            and (not self.strategy['aoe'])
        )

        # Additionally, don't Rake if the current Shred DPE is higher due to
        # trinket procs etc.
        if rake_now:
            rake_dpe, shred_dpe = self.calc_builder_dpe()
            rake_now = (rake_dpe > shred_dpe)

        # Additionally, don't Rake if there is insufficient time to max out
        # our available Glyph of Shred extensions before Rip falls off.
        if rake_now and self.rip_debuff:
            rip_dur = self.rip_end - self.rip_start
            max_rip_dur = self.player.rip_duration + 6*self.player.shred_glyph
            remaining_extensions = (max_rip_dur - rip_dur) / 2
            energy_for_shreds = (
                energy - self.player.rake_cost - 30
                + (self.rip_start + max_rip_dur - time) * 10
                + 60*self.tf_expected_before(time, self.rip_start+max_rip_dur)
            )
            max_shreds_possible = min(
                energy_for_shreds / 42., self.rip_end - (time + 1.0)
            )
            rake_now = (
                (remaining_extensions < 1e-9)
                or (max_shreds_possible > remaining_extensions)
            )

        aoe_rake = (
            self.strategy['aoe'] and (not aoe_mangle) and (cp == 0) and
            ((not self.player.savage_roar) or (self.roar_end - time <= 1.0))
        )
        rake_now = rake_now or aoe_rake

        # Disable Energy pooling for Rake in weaving rotations, since these
        # rotations prioritize weave cpm over Rake uptime.
        pool_for_rake = (
            not (self.strategy['bearweave'] or self.strategy['flowershift'])
            or self.player.t10_4p_bonus
        )

        # Berserk algorithm: time Berserk for just after a Tiger's Fury
        # *unless* we'll lose Berserk uptime by waiting for Tiger's Fury to
        # come off cooldown. The latter exception is necessary for
        # Lacerateweave rotation since TF timings can drift over time.
        berserk_dur = 15 + 5 * self.player.berserk_glyph
        wait_for_tf = (
            (self.player.tf_cd <= berserk_dur) and
            (time + self.player.tf_cd + 1.0 < self.fight_length - berserk_dur)
        )
        berserk_now = (
            self.strategy['use_berserk'] and (self.player.berserk_cd < 1e-9)
            and (not wait_for_tf) and (not self.player.omen_proc)
            and (self.rip_debuff or self.strategy['aoe'])
        )

        # Additionally, for Lacerateweave rotation, postpone the final Berserk
        # of the fight to as late as possible so as to minimize the impact of
        # dropping Lacerate stacks during the Berserk window. Rationale for the
        # 3 second additional leeway given beyond just berserk_dur in the below
        # expression is to be able to fit in a final TF and dump the Energy
        # from it in cases where Berserk and TF CDs are desynced due to drift.
        if (berserk_now and self.strategy['bearweave']
                and self.strategy['lacerate_prio']
                and (self.max_berserk_uses > 1)
                and (self.num_berserk_uses == self.max_berserk_uses - 1)):
            berserk_now = (self.fight_length - time < berserk_dur + 3.0)

        # roar_now = (not self.player.savage_roar) and (cp >= 1)
        # pool_for_roar = (not roar_now) and (cp >= 1) and self.clip_roar(time)
        roar_now = (cp >= 1) and (
            (not self.player.savage_roar) or self.clip_roar(time)
            # or self.emergency_roar(time)
        )
        self.roar_refresh_pending = (
            self.player.savage_roar and (self.roar_end < self.fight_length)
        )

        # Faerie Fire on cooldown for Omen procs. Each second of FF delay is
        # worth ~7 Energy, so it is okay to waste up to 7 Energy to cap when
        # determining whether to cast it vs. dump Energy first. That puts the
        # Energy threshold for FF usage as 107 minus 10 for the Clearcasted
        # special minus 10 for the FF GCD = 87 Energy.
        ff_energy_threshold = (
            self.strategy['berserk_ff_thresh'] if self.player.berserk else 87
        )
        ff_now = (
            (self.player.faerie_fire_cd < 1e-9) and (not self.player.omen_proc)
            and (energy < ff_energy_threshold)
            and ((not rip_now) or (energy < self.player.rip_cost))
        )

        # Also add an end of fight condition to make sure we can spend down our
        # Energy post-FF before the encounter ends. Time to spend is
        # given by 1 second for FF GCD  plus 1 second for Clearcast Shred plus
        # 1 second per 42 Energy that we have after that Clearcast Shred.
        if ff_now:
            # Same end of fight logic can be applied for end of Berserk also,
            # as we don't want to cast even a low Energy FF if it will result
            # in ending Berserk with > 21 Energy and missing a discounted Shred
            if False: # if self.player.berserk:
                boundary_condition = min(self.berserk_end, self.fight_length)
            else:
                boundary_condition = self.fight_length

            max_shreds_without_ff = (
                (energy + (boundary_condition - time) * 10)
                // self.player.shred_cost # floored integer division here
            )
            num_shreds_without_ff = min(
                max_shreds_without_ff, int(boundary_condition - time) + 1
            )
            num_shreds_with_ff = min(
                max_shreds_without_ff + 1, int(boundary_condition - time)
            )
            ff_now = (num_shreds_with_ff > num_shreds_without_ff)

        # Additionally, block Shred and Rake casts if FF is coming off CD in
        # less than a second (and we won't Energy cap by pooling).
        next_ff_energy = (
            energy + 10 * (self.player.faerie_fire_cd + self.latency)
        )
        wait_for_ff = (
            (self.player.faerie_fire_cd < 1.0 - self.strategy['max_ff_delay'])
            and (next_ff_energy < ff_energy_threshold)
            and (not self.player.omen_proc)
            and ((not self.rip_debuff) or (self.rip_end - time > 1.0))
        )

        # First figure out how much Energy we must float in order to be able
        # to refresh our buffs/debuffs as soon as they fall off
        pending_actions = []
        self.rip_refresh_pending = False

        if (self.rip_debuff and (cp == rip_cp) and (not self.block_rip_next)):
            rip_cost = self.player._rip_cost / 2 if self.berserk_expected_at(time, self.rip_end) else self.player._rip_cost
            pending_actions.append((self.rip_end, rip_cost))
            self.rip_refresh_pending = True
        if self.rake_debuff and (self.rake_end < self.fight_length - 9):
            if self.berserk_expected_at(time, self.rake_end):
                pending_actions.append((self.rake_end, 17.5 * pool_for_rake))
            else:
                pending_actions.append((self.rake_end, 35 * pool_for_rake))
        if mangle_refresh_pending:
            base_cost = self.player._mangle_cost
            if self.berserk_expected_at(time, self.mangle_end):
                pending_actions.append((self.mangle_end, 0.5 * base_cost))
            else:
                pending_actions.append((self.mangle_end, base_cost))
        if self.player.savage_roar:
            if self.berserk_expected_at(time, self.roar_end):
                pending_actions.append((self.roar_end, 12.5))
            else:
                pending_actions.append((self.roar_end, 25))

        # Modify pooling logic for AoE rotation
        if self.strategy['aoe']:
            pending_actions = []

            if self.player.savage_roar:
                # First pool for the Roar itself
                pending_actions.append((self.roar_end, self.player.roar_cost))

                # If we don't already have a Combo Point, then also pool for
                # the Mangle or Rake cast to generate it.
                if (cp == 0) and (self.roar_end - time > 1.0):
                    if self.mangle_idol:
                        builder_cost = self.player.mangle_cost
                    else:
                        builder_cost = self.player.rake_cost

                    refresh_time = self.roar_end - 1.0

                    if self.player.faerie_fire_cd > refresh_time - time:
                        pending_actions.append((refresh_time, builder_cost))

        pending_actions.sort()

        # Allow for bearweaving if the next pending action is >= 4.5s away
        furor_cap = min(20 * self.player.furor, 75)
        bearweave_now = self.should_bearweave(time)

        # If we're maintaining Lacerate, then allow for emergency bearweaves
        # if Lacerate is about to fall off even if the above conditions do not
        # apply.
        emergency_bearweave = (
            self.strategy['bearweave'] and self.strategy['lacerate_prio']
            and self.lacerate_debuff
            and (self.lacerate_end - time < 2.5 + self.latency)
            and (self.lacerate_end < self.fight_length)
            and (not self.player.berserk)
        )

        # As an alternative to bearweaving, cast GotW on the raid under
        # analagous conditions to the above. Only difference is that there is
        # more available time/Energy leeway for the technique, since
        # flowershifts take only 3 seconds to execute.
        gcd = 1.5 if self.strategy['daggerweave'] else self.player.spell_gcd
        flowershift_energy = furor_cap - 10 * gcd - 20 * self.latency
        flower_end = time + gcd + 2.5 + 2 * self.latency
        flower_ff_delay = flower_end - (time + self.player.faerie_fire_cd)
        flowershift_now = (
            self.strategy['flowershift'] and (energy <= flowershift_energy)
            and (not self.player.omen_proc)
            and ((not self.rip_refresh_pending) or (self.rip_end >= flower_end))
            and (not self.player.berserk)
            and (not self.tf_expected_before(time, flower_end))
            and (flower_ff_delay <= self.strategy['max_ff_delay'])
        )

        # Also add an end of fight condition to make sure we can spend down our
        # Energy post-flowershift before the encounter ends. Time to spend is
        # given by flower_end plus 1 second per 42 Energy that we have after
        # the Clearcast Shred.
        if flowershift_now:
            energy_to_dump = energy + (flower_end - time) * 10
            flowershift_now = (
                flower_end + energy_to_dump // 42 < self.fight_length
            )

        # Pooling logic section
        floating_energy = 0
        previous_time = time
        tf_pending = False

        for refresh_time, refresh_cost in pending_actions:
            delta_t = refresh_time - previous_time

            if (not tf_pending):
                tf_pending = self.tf_expected_before(time, refresh_time)

                if tf_pending:
                    refresh_cost -= 60

            if delta_t < refresh_cost / 10.:
                floating_energy += refresh_cost - 10 * delta_t
                previous_time = refresh_time
            else:
                previous_time += refresh_cost / 10.

        # If any proc trinkets are due to come off ICD soon, then force pool up
        # to the Energy cap in order to maximize special ability casts with the
        # proc active.
        time_to_cap = time + (100. - energy) / 10.
        trinket_active = False
        pool_for_trinket = False

        for trinket in self.player.proc_trinkets:
            if trinket.special_proc_conditions or (trinket.cooldown == 0):
                continue
            if trinket.active or (not self.rip_debuff):
                trinket_active = True
                pool_for_trinket = False
                break

            earliest_proc = trinket.activation_time + trinket.cooldown
            earliest_proc_end = earliest_proc + trinket.proc_duration

            if ((earliest_proc < time_to_cap)
                    and (earliest_proc_end < self.fight_length)):
                pool_for_trinket = True

        if pool_for_trinket:
            floating_energy = max(floating_energy, 100)

        # Another scenario to force pool is when we have 5 CP, have a pending
        # Roar or Rip refresh soon, and cannot fit in a Bite.
        if ((cp == 5) and (not (bite_before_rip or bite_at_end))
                and (not trinket_active) and self.roar_refresh_pending
                and self.rip_refresh_pending
                and (min(self.roar_end, self.rip_end) < time_to_cap)):
            floating_energy = max(floating_energy, 100)

        excess_e = energy - floating_energy
        time_to_next_action = 0.0

        if (not self.player.cat_form) and (not self.player.bear_form):
            # If the previous GotW cast was unsuccessful and we still have
            # leeway available, then try again. Otherwise, shift back into Cat
            # Form.
            if flowershift_now:
                self.player.flowershift(time)
                next_swing = time + max(
                    self.swing_timer * self.player.weapon_speed,
                    self.player.gcd + 2 * self.latency
                )
                self.update_swing_times(
                    next_swing, self.swing_timer, first_swing=True
                )
            else:
                self.player.ready_to_shift = True
        elif self.player.bear_form:
            # Shift back into Cat Form if (a) our first bear auto procced
            # Clearcasting, or (b) our first bear auto didn't generate enough
            # Rage to Mangle or Maul, or (c) we don't have enough time or
            # Energy leeway to spend an additional GCD in Dire Bear Form.
            shift_now = (
                (energy + 15 + 10 * self.latency > furor_cap)
                or (self.rip_refresh_pending and (self.rip_end < time + 3.0))
                or self.player.berserk
            )
            shift_next = (
                (energy + 30 + 10 * self.latency > furor_cap)
                or (self.rip_refresh_pending and (self.rip_end < time + 4.5))
                or self.player.berserk
            )

            if self.strategy['powerbear']:
                powerbear_now = (not shift_now) and (self.player.rage < 10)
            else:
                powerbear_now = False
                shift_now = shift_now or (self.player.rage < 10)

            # lacerate_now = self.strategy['lacerate_prio'] and (
            #     (not self.lacerate_debuff) or (self.lacerate_stacks < 5)
            #     or (self.lacerate_end - time <= self.strategy['lacerate_time'])
            # )
            build_lacerate = (
                (not self.lacerate_debuff) or (self.lacerate_stacks < 5)
            )
            maintain_lacerate = (not build_lacerate) and (
                (self.lacerate_end - time <= self.strategy['lacerate_time'])
                and ((self.player.rage < 38) or shift_next)
                and (self.lacerate_end < self.fight_length)
            )
            lacerate_now = (
                self.strategy['lacerate_prio']
                and (build_lacerate or maintain_lacerate)
            )
            emergency_lacerate = (
                self.strategy['lacerate_prio'] and self.lacerate_debuff
                and (self.lacerate_end - time < 3.0 + 2 * self.latency)
                and (self.lacerate_end < self.fight_length)
            )

            if (not self.strategy['lacerate_prio']) or (not lacerate_now):
                shift_now = shift_now or self.player.omen_proc

            # Also add an end of fight condition to prevent extending a weave
            # if we don't have enough time to spend the pooled Energy thus far.
            if not shift_now:
                energy_to_dump = energy + 30 + 10 * self.latency
                time_to_dump = 3.0 + self.latency + energy_to_dump // 42
                shift_now = (time + time_to_dump >= self.fight_length)

            # Due to the new Feral changes, Faerie Fire takes priority over
            # anything else in Dire Bear Form if it is off cooldown. The only
            # exception is to wait slightly for an extra Maul before casting it
            # to burn any remaining Rage, since we won't be able to Maul again
            # once Omen is procced in order to save the proc for a Shred.
            bearie_fire_now = ff_now

            if bearie_fire_now and (self.player.rage >= 10):
                delayed_shift_time = (
                    self.swing_times[0] + 1.0 + 2 * self.latency
                )
                rip_conflict = (
                    self.rip_refresh_pending
                    and (self.rip_end < delayed_shift_time + 2.5)
                )
                max_ff_delay = self.strategy['max_ff_delay']
                can_delay_ff = (
                    (energy + 10 * (delayed_shift_time - time) <= furor_cap)
                    and (not rip_conflict)
                    and (self.swing_times[0]+self.latency-time < max_ff_delay)
                )
                bearie_fire_now = not can_delay_ff

            if emergency_lacerate and (self.player.rage >= 13):
                return self.lacerate(time)
            elif bearie_fire_now:
                return self.player.faerie_fire()
            elif shift_now:
                # If we are resetting our swing timer using Albino Snake or a
                # duplicate weapon swap, then do an additional check here to
                # see whether we can delay the shift until the next bear swing
                # goes out in order to maximize the gains from the reset.
                projected_delay = self.swing_times[0] + 2 * self.latency - time
                rip_conflict = (
                    self.rip_refresh_pending and
                    (self.rip_end < time + projected_delay + 1.5)
                )
                next_cat_swing = time + self.latency + self.swing_timer / 2.5
                can_delay_shift = (
                    self.strategy['snek'] # and (not self.player.omen_proc)
                    and (energy + 10 * projected_delay <= furor_cap)
                    and (not rip_conflict)
                    and (self.swing_times[0] < next_cat_swing)
                )

                if can_delay_shift:
                    time_to_next_action = self.swing_times[0] - time
                else:
                    self.player.ready_to_shift = True
            elif powerbear_now:
                self.player.shift(time, powershift=True)
            elif lacerate_now and (self.player.rage >= 13):
                return self.lacerate(time)
            elif (self.player.rage >= 15) and (self.player.mangle_cd < 1e-9):
                return self.mangle(time)
            #elif self.player.rage >= 13:  #We never lacerate anymore
             #   return self.lacerate(time)
            else:
                time_to_next_action = self.swing_times[0] - time
        elif emergency_bearweave:
            self.player.ready_to_shift = True
        elif ff_now:
            return self.player.faerie_fire()
        elif berserk_now:
            self.apply_berserk(time)
            return 0.0
        elif roar_now: # or pool_for_roar:
            # If we have leeway to do so, don't Roar right away and instead
            # pool Energy to reduce how much we clip the buff
            # if pool_for_roar:
            #     roar_now = (
            #         (self.roar_end - time <= self.strategy['max_roar_clip'])
            #         or self.player.omen_proc or (energy >= 90)
            #     )

            # if not roar_now:
            #     time_to_next_action = min(
            #         self.roar_end - self.strategy['max_roar_clip'] - time,
            #         (90. - energy) / 10.
            #     )
            if energy >= self.player.roar_cost:
                self.roar_end = self.player.roar(time)
                return 0.0
            else:
                time_to_next_action = (self.player.roar_cost - energy) / 10.
        elif rip_now:
            if (energy >= self.player.rip_cost) or self.player.omen_proc:
                return self.rip(time)
            time_to_next_action = (self.player.rip_cost - energy) / 10.
        elif bite_now:
            if energy >= self.player.bite_cost:
                return self.player.bite()
            time_to_next_action = (self.player.bite_cost - energy) / 10.
        elif mangle_now and (not wait_for_ff):
            if (energy >= mangle_cost) or self.player.omen_proc:
                return self.mangle(time)
            time_to_next_action = (mangle_cost - energy) / 10.
        elif rake_now and (not wait_for_ff):
            if (energy >= self.player.rake_cost) or self.player.omen_proc:
                return self.rake(time)
            time_to_next_action = (self.player.rake_cost - energy) / 10.
        elif bearweave_now:
            self.player.ready_to_shift = True
        elif flowershift_now and (energy < 42):
            self.player.ready_to_gift = True
        elif self.strategy['aoe']:
            if (excess_e >= self.player.swipe_cost) or self.player.omen_proc:
                return self.player.swipe(self.strategy['num_targets'])
            time_to_next_action = (self.player.swipe_cost - excess_e) / 10.
        elif self.strategy['mangle_spam'] and (not self.player.omen_proc):
            if excess_e >= mangle_cost:
                return self.mangle(time)
            time_to_next_action = (mangle_cost - excess_e) / 10.
        elif (not wait_for_ff):
            if (excess_e >= self.player.shred_cost) or self.player.omen_proc:
                return self.shred()

            # Also Shred if we're about to cap on Energy. Catches some edge
            # cases where floating_energy > 100 due to too many synced timers.
            energy_cap = 77. if self.player.faerie_fire_cd <= 1.0 else 100.

            if energy > energy_cap - self.latency:
                return self.shred()

            time_to_next_action = (self.player.shred_cost - excess_e) / 10.

            # Also Shred rather than pooling for Rake/Rip if (a) Berserk is
            # active, or (b) we have not yet maxed out our Glyph of Shred
            # extensions.
            # max_rip_dur = (
            #     self.player.rip_duration + 6 * self.player.shred_glyph
            # )
            # ignore_pooling = self.player.berserk or (
            #     self.rip_debuff and
            #     (self.rip_end - self.rip_start < max_rip_dur - 1e-9) and
            #     (time + time_to_next_action > self.rip_end - 2)
            # )
            ignore_pooling = self.player.berserk

            # When Lacerateweaving, there are scenarios where Lacerate is
            # synced with other pending actions. When this happens, pooling for
            # the pending action will inevitably lead to capping on Energy,
            # since we will be forced to shift into Dire Bear Form immediately
            # after pooling in order to save the Lacerate. Instead, it is
            # preferable to just Shred and bearweave early.
            next_cast_end = time + time_to_next_action + self.latency + 2.0
            ignore_pooling = ignore_pooling or (
                self.strategy['bearweave'] and self.strategy['lacerate_prio']
                and self.lacerate_debuff
                and (self.lacerate_end - 1.5 - self.latency <= next_cast_end)
            )

            if ignore_pooling:
                if energy >= self.player.shred_cost:
                    return self.shred()
                time_to_next_action = (self.player.shred_cost - energy) / 10.

        # Model in latency when waiting on Energy for our next action
        next_action = time + time_to_next_action

        if pending_actions:
            next_action = min(next_action, pending_actions[0][0])

        # Also schedule an action right at Energy cap to make sure we never
        # accidentally over-cap while waiting on other timers.
        next_action = min(
            next_action, time + (100. - energy) / 10. - self.latency
        )

        # If Lacerateweaving, then also schedule an action just before Lacerate
        # expires to ensure we can save it in time.
        if (self.strategy['bearweave'] and self.strategy['lacerate_prio']
                and self.lacerate_debuff
                and (self.lacerate_end < self.fight_length)
                and (time < self.lacerate_end - 1.5 - 3 * self.latency)):
            next_action = min(
                next_action, self.lacerate_end - 1.5 - 3 * self.latency
            )

        # Schedule an action when Faerie Fire (Feral) is off cooldown next.
        next_action = min(next_action, time + self.player.faerie_fire_cd)

        # If nearing Energy cap, then also schedule an action 1 GCD beforehand.
        if ((energy + 10 * (self.player.faerie_fire_cd + self.latency) >= 87)
                and (self.player.faerie_fire_cd >= 1.0)):
            next_action = min(
                next_action, time + self.player.faerie_fire_cd - 1.0
            )

        self.next_action = next_action + self.latency

        return 0.0

    def update_swing_times(self, time, new_swing_timer, first_swing=False):
        """Generate an updated list of swing times after changes to the swing
        timer have occurred.

        Arguments:
            time (float): Simulation time at which swing timer is changing, in
                seconds.
            new_swing_timer (float): Updated swing timer.
            first_swing (bool): If True, generate a fresh set of swing times
                at the start of a simulation. Defaults False.
        """
        # First calculate the start time for the next swing.
        if first_swing:
            start_time = time
        else:
            frac_remaining = (self.swing_times[0] - time) / self.swing_timer
            start_time = time + frac_remaining * new_swing_timer

        # Now update the internal swing times
        self.swing_timer = new_swing_timer

        if start_time > self.fight_length - self.swing_timer:
            self.swing_times = [
                start_time, start_time + self.swing_timer
            ]
        else:
            self.swing_times = list(np.arange(
                start_time, self.fight_length + self.swing_timer,
                self.swing_timer
            ))

    def apply_haste_buff(self, time, haste_rating_delta):
        """Perform associated bookkeeping when the player Haste Rating is
        modified.

        Arguments:
            time (float): Simulation time in seconds.
            haste_rating_delta (int): Amount by which the player Haste Rating
                changes.
        """
        new_haste_rating = haste_rating_delta + sim_utils.calc_haste_rating(
            self.swing_timer, multiplier=self.haste_multiplier,
            cat_form=not self.player.bear_form
        )
        new_swing_timer = sim_utils.calc_swing_timer(
            new_haste_rating, multiplier=self.haste_multiplier,
            cat_form=not self.player.bear_form
        )
        self.update_swing_times(time, new_swing_timer)
        self.player.update_spell_gcd(new_haste_rating)

    def apply_tigers_fury(self, time):
        """Apply Tiger's Fury buff and document if requested.

        Arguments:
            time (float): Simulation time when Tiger's Fury is cast, in
                seconds
        """
        self.player.energy = min(100, self.player.energy + 60)
        self.params['tigers_fury'] = True
        self.player.calc_damage_params(**self.params)
        self.tf_end = time + 6.
        self.player.tf_cd = 30.
        self.next_action = time + self.latency
        self.proc_end_times.append(time + 30.)
        self.proc_end_times.sort()

        if self.log:
            self.combat_log.append(
                self.gen_log(time, "Tiger's Fury", 'applied')
            )

    def drop_tigers_fury(self, time):
        """Remove Tiger's Fury buff and document if requested.

        Arguments:
            time (float): Simulation time when Tiger's Fury fell off, in
                seconds. Used only for logging.
        """
        self.params['tigers_fury'] = False
        self.player.calc_damage_params(**self.params)

        if self.log:
            self.combat_log.append(
                self.gen_log(time, "Tiger's Fury", 'falls off')
            )

    def apply_berserk(self, time, prepop=False):
        """Apply Berserk buff and document if requested.

        Arguments:
            time (float): Simulation time when Berserk is cast, in seconds.
            prepop (bool): Whether Berserk is pre-popped 1 second before the
                start of combat rather than in the middle of the fight.
                Defaults False.
        """
        self.player.berserk = True
        self.player.set_ability_costs()
        self.player.gcd = 1.0 * (not prepop)
        self.berserk_end = time + 15. + 5 * self.player.berserk_glyph
        self.player.berserk_cd = 180. - prepop
        self.num_berserk_uses += 1

        if self.log:
            self.combat_log.append(
                self.gen_log(time, 'Berserk', 'applied')
            )

        # if self.params['tigers_fury']:
        #     self.drop_tigers_fury(time)

    def drop_berserk(self, time):
        """Remove Berserk buff and document if requested.

        Arguments:
            time (float): Simulation time when Berserk fell off, in seconds.
                Used only for logging.
        """
        self.player.berserk = False
        self.player.set_ability_costs()

        if self.log:
            self.combat_log.append(
                self.gen_log(time, 'Berserk', 'falls off')
            )

    def apply_bleed_damage(
        self, base_tick_damage, crit_chance, ability_name, sr_snapshot, time
    ):
        """Apply a periodic damage tick from an active bleed effect.

        Arguments:
            base_tick_damage (float): Damage per tick of the bleed prior to
                Mangle or Savage Roar modifiers.
            crit_chance (float): Snapshotted critical strike chance of the
                bleed, between 0 and 1.
            ability_name (str): Name of the bleed ability. Used for combat
                logging.
            sr_snapshot (bool): Whether Savage Roar was active when the bleed
                was initially cast.
            time (float): Simulation time, in seconds. Used for combat logging.

        Returns:
            tick_damage (float): Final damage done by the bleed tick.
        """
        tick_damage = base_tick_damage * (1 + 0.3 * self.mangle_debuff)

        if (crit_chance > 0) and self.player.primal_gore:
            tick_damage, _, _ = sim_utils.calc_yellow_damage(
                tick_damage, tick_damage, 0.0, crit_chance,
                crit_multiplier=self.player.calc_crit_multiplier()
            )

        self.player.dmg_breakdown[ability_name]['damage'] += tick_damage

        if sr_snapshot:
            self.player.dmg_breakdown['Savage Roar']['damage'] += (
                self.player.roar_fac * tick_damage
            )
            tick_damage *= 1 + self.player.roar_fac

        if self.log:
            self.combat_log.append(
                self.gen_log(time, ability_name + ' tick', '%d' % tick_damage)
            )

        # Since a handful of proc effects trigger only on periodic damage, we
        # separately check for those procs here.
        for trinket in self.player.proc_trinkets:
            if trinket.periodic_only:
                trinket.check_for_proc(False, True)
                tick_damage += trinket.update(time, self.player, self)

        
        if self.player.t8_2p_bonus and time - 15 >= self.t8_2p_icd:
            t8_2p_proc = np.random.rand()
            if t8_2p_proc < 0.02:
                self.player.omen_proc = True
                self.t8_2p_icd = time

        return tick_damage

    def run(self, log=False):
        """Run a simulated trajectory for the fight.

        Arguments:
            log (bool): If True, generate a full combat log of events within
                the simulation. Defaults False.

        Returns:
            times, damage, energy, combos: Lists of the time,
                total damage done, player energy, and player combo points at
                each simulated event within the fight duration.
            damage_breakdown (collection.OrderedDict): Dictionary containing a
                breakdown of the number of casts and damage done by each player
                ability.
            aura_stats (list of lists): Breakdown of the number of activations
                and total uptime of each buff aura applied from trinkets and
                other cooldowns.
            combat_log (list of lists): Each entry is a list [time, event,
                outcome, energy, combo points, mana] all formatted as strings.
                Only output if log == True.
        """
        # Reset player to fresh fight
        self.player.reset()
        self.mangle_debuff = False
        self.rip_debuff = False
        self.rip_refresh_pending = False
        self.rake_debuff = False
        self.lacerate_debuff = False
        self.params['tigers_fury'] = False
        self.next_action = 0.0

        # Configure combat logging if requested
        self.log = log

        if self.log:
            self.player.log = True
            self.combat_log = []
        else:
            self.player.log = False

        # Same thing for swing times, except that the first swing will occur at
        # most 100 ms after the first special just to simulate some latency and
        # avoid errors from Omen procs on the first swing.
        swing_timer_start = 0.1 * np.random.rand()
        self.update_swing_times(
            swing_timer_start, self.player.swing_timer, first_swing=True
        )

        # Reset all trinkets to fresh state
        self.proc_end_times = []

        for trinket in self.trinkets:
            trinket.reset()

        # Track 2pT8 icd end time
        self.t8_2p_icd = 0

        # Calculate maximum Berserk cooldown uses for given fight length
        self.max_berserk_uses = 1 + int((self.fight_length - 4.0) // 180)
        self.num_berserk_uses = 0

        # If a bear tank is providing Mangle uptime for us, then flag the
        # debuff as permanently on.
        if self.strategy['bear_mangle']:
            self.mangle_debuff = True
            self.mangle_end = np.inf

        # Pre-pop Berserk if requested
        if self.strategy['use_berserk'] and self.strategy['prepop_berserk']:
            self.apply_berserk(-1.0, prepop=True)

        # Pre-proc Clearcasting if requested
        if self.strategy['preproc_omen'] and self.player.omen:
            self.player.omen_proc = True
            # self.player.faerie_fire_cd = 5.0 - self.player.berserk

        # If Idol swapping, then start fight with Mangle or Rip Idol equipped
        if self.strategy['mangle_idol_swap']:
            self.mangle_idol.equipped = True
            self.player.shred_bonus = 0
            self.player.rip_bonus = 0
            self.player.calc_damage_params(**self.params)
        elif self.strategy['idol_swap']:
            self.player.shred_bonus = 0
            self.player.rip_bonus = self.rip_bonus
            self.player.calc_damage_params(**self.params)

        # Create placeholder for time to OOM if the player goes OOM in the run
        self.time_to_oom = None

        # Create empty lists of output variables
        times = []
        damage = []
        energy = []
        combos = []

        # Run simulation
        time = 0.0
        previous_time = 0.0
        num_hot_ticks = 0

        while time <= self.fight_length:

            # Update player Mana and Energy based on elapsed simulation time
            delta_t = time - previous_time
            self.player.regen(delta_t)

            # Tabulate all damage sources in this timestep
            dmg_done = 0.0

            # Decrement cooldowns by time since last event
            self.player.gcd = max(0.0, self.player.gcd - delta_t)
            self.player.ilotp_icd = max(0.0, self.player.ilotp_icd - delta_t)
            self.player.rune_cd = max(0.0, self.player.rune_cd - delta_t)
            self.player.tf_cd = max(0.0, self.player.tf_cd - delta_t)
            self.player.berserk_cd = max(0.0, self.player.berserk_cd - delta_t)
            self.player.enrage_cd = max(0.0, self.player.enrage_cd - delta_t)
            self.player.mangle_cd = max(0.0, self.player.mangle_cd - delta_t)
            self.player.faerie_fire_cd = max(
                0.0, self.player.faerie_fire_cd - delta_t
            )

            if (self.player.five_second_rule
                    and (time - self.player.last_shift >= 5)):
                self.player.five_second_rule = False

            # Check if Tiger's Fury fell off
            if self.params['tigers_fury'] and (time >= self.tf_end):
                self.drop_tigers_fury(self.tf_end)

            # Check if Berserk fell off
            if self.player.berserk and (time >= self.berserk_end):
                self.drop_berserk(self.berserk_end)

            # Check if Mangle fell off
            if self.mangle_debuff and (time >= self.mangle_end):
                self.mangle_debuff = False

                if self.log:
                    self.combat_log.append(
                        self.gen_log(self.mangle_end, 'Mangle', 'falls off')
                    )

            # Check if Savage Roar fell off
            if self.player.savage_roar and (time >= self.roar_end):
                self.player.savage_roar = False

                if log:
                    self.combat_log.append(
                        self.gen_log(self.roar_end, 'Savage Roar', 'falls off')
                    )

            # Check if a Rip tick happens at this time
            if self.rip_debuff and (time >= self.rip_ticks[0]):
                dmg_done += self.apply_bleed_damage(
                    self.rip_damage, self.rip_crit_bonus_chance, 'Rip',
                    self.rip_sr_snapshot, time
                )
                self.rip_ticks.pop(0)

            # Check if Rip fell off
            if self.rip_debuff and (time > self.rip_end - 1e-9):
                self.rip_debuff = False

                if self.log:
                    self.combat_log.append(
                        self.gen_log(self.rip_end, 'Rip', 'falls off')
                    )

            # Check if a Rake tick happens at this time
            if self.rake_debuff and (time >= self.rake_ticks[0]):
                dmg_done += self.apply_bleed_damage(
                    self.rake_damage,
                    self.rake_crit_chance * self.player.t10_4p_bonus,
                    'Rake',
                    self.rake_sr_snapshot,
                    time
                )
                self.rake_ticks.pop(0)

                if self.rake_idol:
                    self.rake_idol.check_for_proc(False, True)
                    self.rake_idol.update(time, self.player, self)

            # Check if Rake fell off
            if self.rake_debuff and (time > self.rake_end - 1e-9):
                self.rake_debuff = False

                if self.log:
                    self.combat_log.append(
                        self.gen_log(self.rake_end, 'Rake', 'falls off')
                    )

            # Check if a Lacerate tick happens at this time
            if (self.lacerate_debuff and self.lacerate_ticks
                    and (time >= self.lacerate_ticks[0])):
                self.last_lacerate_tick = time
                dmg_done += self.apply_bleed_damage(
                    self.lacerate_damage, self.lacerate_crit_chance,
                    'Lacerate', False, time
                )
                self.lacerate_ticks.pop(0)

                if self.rake_idol:
                    self.rake_idol.check_for_proc(False, True)
                    self.rake_idol.update(time, self.player, self)

            # Check if Lacerate fell off
            if self.lacerate_debuff and (time > self.lacerate_end - 1e-9):
                self.lacerate_debuff = False

                if self.log:
                    self.combat_log.append(self.gen_log(
                        self.lacerate_end, 'Lacerate', 'falls off'
                    ))

            # Roll for Revitalize procs at the pre-calculated frequency
            if time >= self.revitalize_frequency * (num_hot_ticks + 1):
                num_hot_ticks += 1

                if np.random.rand() < 0.15:
                    if self.player.cat_form:
                        self.player.energy = min(100, self.player.energy + 8)
                    else:
                        self.player.rage = min(100, self.player.rage + 4)

                    if self.log:
                        self.combat_log.append(
                            self.gen_log(time, 'Revitalize', 'applied')
                        )

            # Activate or deactivate trinkets if appropriate
            for trinket in self.trinkets:
                dmg_done += trinket.update(time, self.player, self)

            # Use Enrage if appropriate
            if (self.player.bear_form and (self.player.enrage_cd < 1e-9)
                    and (time < self.player.last_shift + 1.5 + 1e-9)):
                self.player.rage = min(100, self.player.rage + 20)
                self.player.enrage = True
                self.player.enrage_cd = 60.

                if self.log:
                    self.combat_log.append(
                        self.gen_log(time, 'Enrage', 'applied')
                    )

            # Check if a melee swing happens at this time
            if time == self.swing_times[0]:
                prior_omen_proc = self.player.omen_proc

                if self.player.cat_form:
                    dmg_done += self.player.swing()

                    # If daggerweaving, swap back to normal weapon after the
                    # swing goes out.
                    if self.player.dagger_equipped:
                        self.player.attack_power += (
                            self.strategy['dagger_ep_loss']
                            * self.player.ap_mod
                        )
                        self.player.calc_damage_params(**self.params)
                        self.player.dagger_equipped = False
                else:
                    # If we will have enough time and Energy leeway to stay in
                    # Dire Bear Form once the GCD expires, then only Maul if we
                    # will be left with enough Rage to cast Mangle or Lacerate
                    # on that global.
                    furor_cap = min(20 * self.player.furor, 75)
                    energy_leeway = (
                        furor_cap - 15
                        - 10 * (self.player.gcd + self.latency)
                    )
                    shift_next = (self.player.energy > energy_leeway)

                    if self.rip_refresh_pending:
                        shift_next = shift_next or (
                            self.rip_end < time + self.player.gcd + 3.0
                        )

                    if self.strategy['lacerate_prio']:
                        lacerate_leeway = (
                            self.player.gcd + self.strategy['lacerate_time']
                        )
                        lacerate_next = (
                            (not self.lacerate_debuff)
                            or (self.lacerate_stacks < 5)
                            or (self.lacerate_end - time <= lacerate_leeway)
                        )
                        emergency_leeway = (
                            self.player.gcd + 3.0 + 2 * self.latency
                        )
                        emergency_lacerate_next = (
                            self.lacerate_debuff and
                            (self.lacerate_end - time <= emergency_leeway)
                        )
                        mangle_next = (not lacerate_next) and (
                            (not self.mangle_debuff)
                            or (self.mangle_end < time + self.player.gcd + 3.0)
                            or (time - self.player.last_shift < 1.5)
                        )
                    else:
                        mangle_next = (self.player.mangle_cd < self.player.gcd)
                        lacerate_next = self.lacerate_debuff and (
                            (self.lacerate_stacks < 5) or
                            (self.lacerate_end < time + self.player.gcd + 4.5)
                        )
                        emergency_lacerate_next = False

                    if emergency_lacerate_next:
                        maul_rage_thresh = 23
                    elif shift_next:
                        maul_rage_thresh = 10
                    elif mangle_next:
                        maul_rage_thresh = 25
                    elif lacerate_next:
                        maul_rage_thresh = 23
                    else:
                        maul_rage_thresh = 10

                    if self.player.rage >= maul_rage_thresh and not self.player.omen_proc: #gonna block this if omen
                        dmg_done += self.player.maul(self.mangle_debuff)
                    else:
                        dmg_done += self.player.swing()

                self.swing_times.pop(0)

                if self.log:
                    self.combat_log.append(
                        ['%.3f' % time] + self.player.combat_log
                    )

                # If the swing/Maul resulted in an Omen proc, then schedule the
                # next player decision based on latency.
                if self.player.omen_proc and (not prior_omen_proc):
                    self.next_action = time + self.latency

            # Check if we're able to act, and if so execute the optimal cast.
            self.player.combat_log = None

            if (self.player.gcd < 1e-9) and (time >= self.next_action):
                dmg_done += self.execute_rotation(time)

            # Append player's log to running combat log
            if self.log and self.player.combat_log:
                self.combat_log.append(
                    ['%.3f' % time] + self.player.combat_log
                )

            # If we entered Dire Bear Form, Tiger's Fury fell off
            if self.params['tigers_fury'] and (not self.player.cat_form):
                self.drop_tigers_fury(time)

            # If a trinket proc occurred from a swing or special, apply it
            for trinket in self.trinkets:
                dmg_done += trinket.update(time, self.player, self)

            # If a proc ended at this timestep, remove it from the list
            if self.proc_end_times and (time == self.proc_end_times[0]):
                self.proc_end_times.pop(0)

            # If our Energy just dropped low enough, then cast Tiger's Fury
            #tf_energy_thresh = 30
            leeway_time = max(self.player.gcd, self.latency)
            tf_energy_thresh = 40 - 10 * (leeway_time + self.player.omen_proc)
            tf_now = (
                (self.player.energy < tf_energy_thresh)
                and (self.player.tf_cd < 1e-9) and (not self.player.berserk)
                and self.player.cat_form and (not self.player.ready_to_shift)
            )

            # If Lacerateweaving, then delay Tiger's Fury if Lacerate is due to
            # expire within 3 GCDs (two cat specials + shapeshift), since we
            # won't be able to spend down our Energy fast enough to avoid
            # Energy capping otherwise.
            if self.strategy['bearweave'] and self.strategy['lacerate_prio']:
                next_possible_lac = time + leeway_time + 3.5 + self.latency
                tf_now = tf_now and (
                    (not self.lacerate_debuff)
                    or (self.lacerate_end > next_possible_lac)
                    or (self.lacerate_end > self.fight_length)
                )

            if tf_now:
                # If Berserk is available, then pool to 30 Energy before
                # casting TF to maximize Berserk efficiency.
                # if self.player.berserk_cd <= leeway_time:
                #     delta_e = tf_energy_thresh - 10 - self.player.energy

                #     if delta_e < 1e-9:
                #         self.apply_tigers_fury(time)
                #     else:
                #         self.next_action = time + delta_e / 10.
                # else:
                #     self.apply_tigers_fury(time)
                self.apply_tigers_fury(time)

            # If Mangle Idol weaving is configured and we just cast a Cat Form
            # special ability, then bundle an Idol swap with the cast if we
            # expect to bearweave on our next GCD.
            # mangle_bear_soon = (
            #     (not self.strategy['lacerate_prio'])
            #     # or (self.lacerate_debuff and (self.lacerate_stacks >= 4))
            # )

            # if ((self.player.gcd == 1.0) and self.strategy['bearweave']
            #         and self.strategy['mangle_idol_swap']
            #         and self.should_bearweave(time, future_time=time + 1.5)
            #         and mangle_bear_soon and (not self.mangle_idol.equipped)):
            #     self.player.shred_bonus = 0
            #     self.player.rip_bonus = 0
            #     self.player.calc_damage_params(**self.params)
            #     self.player.gcd = 1.5
            #     self.update_swing_times(
            #         time + self.swing_timer, self.swing_timer, first_swing=True
            #     )
            #     self.mangle_idol.equipped = True

            #     if self.log:
            #         self.combat_log.append(
            #             self.gen_log(time, 'Mangle Idol', 'equipped')
            #         )

            # Log current parameters
            times.append(time)
            damage.append(dmg_done)
            energy.append(self.player.energy)
            combos.append(self.player.combo_points)

            # Update time
            previous_time = time
            next_swing = self.swing_times[0]
            next_action = max(time + self.player.gcd, self.next_action)
            time = min(next_action, next_swing)

            if self.rip_debuff:
                time = min(time, self.rip_ticks[0])
            if self.rake_debuff:
                time = min(time, self.rake_ticks[0])
            if self.lacerate_debuff and self.lacerate_ticks:
                time = min(time, self.lacerate_ticks[0])
            if self.proc_end_times:
                time = min(time, self.proc_end_times[0])

        # Perform a final update on trinkets at the exact fight end for
        # accurate uptime calculations. Manually deactivate any trinkets that
        # are still up, and consolidate the aura uptimes.
        aura_stats = []

        for trinket in self.trinkets:
            trinket.update(self.fight_length, self.player, self)

            try:
                if trinket.active:
                    trinket.deactivate(
                        self.player, self, time=self.fight_length
                    )

                aura_stats.append(
                    [trinket.proc_name, trinket.num_procs, trinket.uptime]
                )
            except AttributeError:
                pass

        output = (
            times, damage, energy, combos, self.player.dmg_breakdown,
            aura_stats
        )

        if self.log:
            output += (self.combat_log,)

        return output

    def iterate(self, *args):
        """Perform one iteration of a multi-replicate calculation with a
        randomized fight length.

        Returns:
            avg_dps (float): Average DPS on this iteration.
            dmg_breakdown (dict): Breakdown of cast count and damage done by
                each player ability on this iteration.
            aura_stats (list of lists): Breakdown of proc count and total
                uptime of each player cooldown on this iteration.
            time_to_oom (float): Time at which player went oom in this
                iteration. If the player did not oom, then the fight length
                used in this iteration will be returned instead.
        """
        # Since we're getting the same snapshot of the Simulation object
        # when multiple iterations are run in parallel, we need to generate a
        # new random seed.
        np.random.seed()

        # Randomize fight length to avoid haste clipping effects. We will
        # use a normal distribution centered around the target length, with
        # a standard deviation of 1 second (unhasted swing timer). Impact
        # of the choice of distribution needs to be assessed...
        base_fight_length = self.fight_length
        randomized_fight_length = base_fight_length + np.random.randn()
        self.fight_length = randomized_fight_length

        _, damage, _, _, dmg_breakdown, aura_stats = self.run()
        avg_dps = np.sum(damage) / self.fight_length
        self.fight_length = base_fight_length

        if self.time_to_oom is None:
            oom_time = randomized_fight_length
        else:
            oom_time = self.time_to_oom

        return avg_dps, dmg_breakdown, aura_stats, oom_time

    def run_replicates(self, num_replicates, detailed_output=False):
        """Perform several runs of the simulation in order to collect
        statistics on performance.

        Arguments:
            num_replicates (int): Number of replicates to run.
            detailed_output (bool): Whether to consolidate details about cast
                and mana statistics in addition to DPS values. Defaults False.

        Returns:
            dps_vals (np.ndarray): Array containing average DPS of each run.
            cast_summary (collections.OrderedDict): Dictionary containing
                averaged statistics for the number of casts and total damage
                done by each player ability over the simulated fight length.
                Output only if detailed_output == True.
            aura_summary (list of lists): Averaged statistics for the number of
                procs and total uptime of each player cooldown over the
                simulated fight length. Output only if detailed_output == True.
            oom_times (np.ndarray): Array containing times at which the player
                went oom in each run. Output only if detailed_output == True.
                If the player did not oom in a run, the corresponding entry
                will be the total fight length.
        """
        # Make sure damage and mana parameters are up to date
        self.player.calc_damage_params(**self.params)
        self.player.set_mana_regen()

        # Run replicates and consolidate results
        dps_vals = np.zeros(num_replicates)

        if detailed_output:
            oom_times = np.zeros(num_replicates)

        # Create pool of workers to run replicates in parallel
        pool = multiprocessing.Pool(processes=psutil.cpu_count(logical=False))
        i = 0

        for output in pool.imap(self.iterate, range(num_replicates)):
            avg_dps, dmg_breakdown, aura_stats, time_to_oom = output
            dps_vals[i] = avg_dps

            if not detailed_output:
                i += 1
                continue

            # Consolidate damage breakdown for the fight
            if i == 0:
                cast_sum = copy.deepcopy(dmg_breakdown)
                aura_sum = copy.deepcopy(aura_stats)
            else:
                for ability in cast_sum:
                    for key in cast_sum[ability]:
                        val = dmg_breakdown[ability][key]
                        cast_sum[ability][key] = (
                            (cast_sum[ability][key] * i + val) / (i + 1)
                        )
                for row in range(len(aura_sum)):
                    for col in [1, 2]:
                        val = aura_stats[row][col]
                        aura_sum[row][col] = (
                            (aura_sum[row][col] * i + val) / (i + 1)
                        )

            # Consolidate oom time
            oom_times[i] = time_to_oom
            i += 1

        pool.close()

        if not detailed_output:
            return dps_vals

        return dps_vals, cast_sum, aura_sum, oom_times

    def calc_deriv(self, num_replicates, param, increment, base_dps_sample):
        """Calculate DPS increase after incrementing a player stat.

        Arguments:
            num_replicates (int): Number of replicates to run.
            param (str): Player attribute to increment.
            increment (float): Magnitude of stat increment.
            base_dps_sample (np.ndarray): Pre-calculated statistical sample of
                base DPS before stat increments.

        Returns:
            dps_delta (float): Average DPS increase after the stat increment.
                The Player attribute will be reset to its original value once
                the calculation is finished.
            error_bar (float): Bootstrapped standard deviation of the DPS
                increase.
        """
        # Increment the stat
        original_value = getattr(self.player, param)
        setattr(self.player, param, original_value + increment)

        # For Expertise increments, implementation details demand we
        # update both 'miss_chance' and 'dodge_chance'
        if param == 'dodge_chance':
            self.player.miss_chance += increment

        # For Agility increments, also augment Attack Power and Crit
        if param == 'agility':
            self.player.attack_power += self.player.ap_mod * increment
            self.player.crit_chance += increment / 83.33 / 100.

        # For Hit chance increments, also augment Spell Hit chance
        if param == 'hit_chance':
            self.player.spell_hit_chance += increment * 32.79 / 26.23

        # For Crit chance increments, also augment Spell Crit chance
        if param == 'crit_chance':
            self.player.spell_crit_chance += increment

        # Calculate DPS
        dps_vals = self.run_replicates(num_replicates)
        avg_dps = np.mean(dps_vals)

        # Reset the stat to original value
        setattr(self.player, param, original_value)

        if param == 'dodge_chance':
            self.player.miss_chance -= increment

        if param == 'agility':
            self.player.attack_power -= self.player.ap_mod * increment
            self.player.crit_chance -= increment / 83.33 / 100.

        if param == 'hit_chance':
            self.player.spell_hit_chance -= increment * 32.79 / 26.23

        if param == 'crit_chance':
            self.player.spell_crit_chance -= increment

        # Error analysis
        dps_delta = np.mean(dps_vals) - np.mean(base_dps_sample)
        error_bar = sim_utils.calc_ep_variance(
            base_dps_sample, dps_vals, num_replicates, bootstrap=False
        )

        return np.array([dps_delta, error_bar])

    def calc_stat_weights(
            self, num_replicates, base_dps_sample=None, agi_mod=1.0
    ):
        """Calculate performance derivatives for AP, hit, crit, and haste.

        Arguments:
            num_replicates (int): Number of replicates to run.
            base_dps_sample (np.ndarray): If provided, use a pre-calculated
                statistical sample for the base DPS before stat increments.
                Defaults to calculating base DPS from scratch.
            agi_mod (float): Multiplier for primary attributes to use for
                determining Agility weight. Defaults to 1.0

        Returns:
            dps_deltas (dict): Dictionary containing DPS increase from 1 AP,
                1% hit, 1% expertise, 1% crit, 1% haste, 1 Agility, 1 Armor Pen
                Rating, and 1 Weapon Damage.
            stat_weights (dict): Dictionary containing normalized stat weights
                for 1% hit, 1% expertise, 1% crit, 1% haste, 1 Agility, 1 Armor
                Pen Rating, and 1 Weapon Damage relative to 1 AP.
        """
        # First store base DPS and deltas after each stat increment
        start_time = time.time()
        print('\n')
        dps_deltas = {}

        if base_dps_sample is None:
            base_dps_sample = self.run_replicates(num_replicates)

        base_dps = np.mean(base_dps_sample)

        # For all stats, we will use a much larger increment than +1 in order
        # to see sufficient DPS increases above the simulation noise. We will
        # then linearize the increase down to a +1 increment for weight
        # calculation. This approximation is accurate as long as DPS is linear
        # in each stat up to the larger increment that was used.

        # For AP, we will use an increment of +80 AP. We also scale the
        # increase by a factor of 1.1 to account for HotW
        dps_deltas['Attack Power'] = 1.0/80.0 * self.calc_deriv(
            num_replicates, 'attack_power', 80 * self.player.ap_mod,
            base_dps_sample
        )

        # For hit and crit, we will use an increment of 2%.

        # For hit, we reduce miss chance by 2% if well below hit cap, and
        # increase miss chance by 2% when already capped or close.
        # Assumption made here is that the player should only be concerned
        # with the melee hit
        sign = 1 - 2 * int(
            self.player.miss_chance - self.player.dodge_chance > 0.02
        )
        dps_deltas['Hit Rating'] = -0.5 / 32.79 * sign * self.calc_deriv(
            num_replicates, 'miss_chance', sign * 0.02, base_dps_sample
        )

        # For expertise, we mimic hit, except with dodge.
        sign = 1 - 2 * int(self.player.dodge_chance > 0.02)
        dps_deltas['Expertise Rating'] = -0.5 / 32.79 * sign * self.calc_deriv(
            num_replicates, 'dodge_chance', sign * 0.02, base_dps_sample
        )

        # Crit is a simple increment
        dps_deltas['Critical Strike Rating'] = 0.5 / 45.91 * self.calc_deriv(
            num_replicates, 'crit_chance', 0.02, base_dps_sample
        )

        # For haste we will use an increment of 4%. (Note that this is 4% in
        # one slot and not four individual 1% buffs.) We implement the
        # increment by reducing the player swing timer.
        base_haste_rating = sim_utils.calc_haste_rating(
            self.player.swing_timer, multiplier=self.haste_multiplier
        )
        swing_delta = self.player.swing_timer - sim_utils.calc_swing_timer(
            base_haste_rating + 100.84, multiplier=self.haste_multiplier
        )
        dps_deltas['Haste Rating'] = 0.25 / 25.21 * self.calc_deriv(
            num_replicates, 'swing_timer', -swing_delta, base_dps_sample
        )

        # Due to bearweaving, separate Agility weight calculation is needed
        dps_deltas['Agility'] = 1.0/65.0 * self.calc_deriv(
            num_replicates, 'agility', 65 * agi_mod, base_dps_sample
        )

        # For armor pen, we use an increment of 65 Rating. Similar to hit,
        # the sign of the delta depends on if we're near the 1399 cap.
        sign = 1 - 2 * int(self.player.armor_pen_rating > 1334)
        dps_deltas['Armor Pen Rating'] = 1./65. * sign * self.calc_deriv(
            num_replicates, 'armor_pen_rating', sign * 65, base_dps_sample
        )

        # For weapon damage, we use an increment of 65
        dps_deltas['Weapon Damage'] = 1./65. * self.calc_deriv(
            num_replicates, 'bonus_damage', 65, base_dps_sample
        )

        # Calculate normalized stat weights
        stat_weights = {}

        for stat in dps_deltas:
            # Print results of error analysis
            ep, std = dps_deltas[stat]
            diag_str = (
                '%s: EP = %.2f +/- %.2f, scale up increment by %.2fx for '
                'better results'
            ) % (stat, ep, 2 * abs(std), abs(std) / 0.005)
            print(diag_str)
            dps_deltas[stat] = ep

            if stat != 'Attack Power':
                stat_weights[stat] = (
                    dps_deltas[stat] / dps_deltas['Attack Power']
                )

        calculation_time = (time.time() - start_time) / 60.
        print('Total calculation time: %.1f minutes' % calculation_time)
        return dps_deltas, stat_weights
