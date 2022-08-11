"""Code for modeling non-static trinkets in feral DPS simulation."""

import numpy as np
import wotlk_cat_sim as ccs


class Trinket():

    """Keeps track of activation times and cooldowns for an equipped trinket,
    updates Player and Simulation parameters when the trinket is active, and
    determines when procs or trinket activations occur."""

    def __init__(
        self, stat_name, stat_increment, proc_name, proc_duration, cooldown
    ):
        """Initialize a generic trinket with key parameters.

        Arguments:
            stat_name (str or list): Name of the Player attribute that will be
                modified by the trinket activation. Must be a valid attribute
                of the Player class that can be modified. The one exception is
                haste_rating, which is separately handled by the Simulation
                object when updating timesteps for the sim. A list of strings
                can be provided instead, in which case every stat in the list
                will be modified during the trinket activation.
            stat_increment (float or np.ndarray): Amount by which the Player
                attribute is changed when the trinket is active. If multiple
                stat names are specified, then this must be a numpy array of
                equal length to the number of stat names.
            proc_name (str): Name of the buff that is applied when the trinket
                is active. Used for combat logging.
            proc_duration (int): Duration of the buff, in seconds.
            cooldown (int): Internal cooldown before the trinket can be
                activated again, either via player use or procs.
        """
        self.stat_name = stat_name
        self.stat_increment = stat_increment
        self.proc_name = proc_name
        self.proc_duration = proc_duration
        self.cooldown = cooldown
        self.reset()

    def reset(self):
        """Set trinket to fresh inactive state with no cooldown remaining."""
        self.activation_time = -np.inf
        self.active = False
        self.can_proc = True
        self.num_procs = 0
        self.uptime = 0.0
        self.last_update = 0.0

    def modify_stat(self, time, player, sim, increment):
        """Change a player stat when a trinket is activated or deactivated.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
            increment (float or np.ndarray): Quantity to add to the player's
                existing stat value(s).
        """
        # Convert stat name and stat increment to arrays if they are scalars
        stat_names = np.atleast_1d(self.stat_name)
        increments = np.atleast_1d(increment)

        for index, stat_name in enumerate(stat_names):
            self._modify_stat(time, player, sim, stat_name, increments[index])

    @staticmethod
    def _modify_stat(time, player, sim, stat_name, increment):
        """Contains the actual stat modification functionality for a single
        stat. Called by the wrapper function, which handles potentially
        iterating through multiple stats to be modified."""
        # Haste procs get handled separately from other raw stat buffs
        if stat_name == 'haste_rating':
            sim.apply_haste_buff(time, increment)
        else:
            old_value = getattr(player, stat_name)
            setattr(player, stat_name, old_value + increment)

            # Recalculate damage parameters when player stats change
            player.calc_damage_params(**sim.params)

    def activate(self, time, player, sim):
        """Activate the trinket buff upon player usage or passive proc.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.

        Returns:
            damage_done (float): Any instant damage that is dealt when the
                trinket is activated. Defaults to 0 for standard trinkets, but
                custom subclasses can implement fixed damage procs that would
                be calculated in this method.
        """
        self.activation_time = time
        self.deactivation_time = time + self.proc_duration
        self.modify_stat(time, player, sim, self.stat_increment)
        sim.proc_end_times.append(self.deactivation_time)

        # In the case of a second trinket being used, the proc end time can
        # sometimes be earlier than that of the first trinket, so the list of
        # end times needs to be sorted.
        sim.proc_end_times.sort()

        # Mark trinket as active
        self.active = True
        self.can_proc = False
        self.num_procs += 1

        # Log if requested
        if sim.log:
            sim.combat_log.append(sim.gen_log(time, self.proc_name, 'applied'))

        # Return default damage dealt of 0
        return 0.0

    def deactivate(self, player, sim, time=None):
        """Deactivate the trinket buff when the duration has expired.

        Arguments:
            player (tbc_cat_sim.Player): Player object whose attributes will be
                restored to their original values.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
            time (float): Time at which the trinket is deactivated. Defaults to
                the stored time for automatic deactivation.
        """
        if time is None:
            time = self.deactivation_time

        self.modify_stat(time, player, sim, -self.stat_increment)
        self.active = False

        if sim.log:
            sim.combat_log.append(
                sim.gen_log(time, self.proc_name, 'falls off')
            )

    def update(self, time, player, sim, allow_activation=True):
        """Check for a trinket activation or deactivation at the specified
        simulation time, and perform associated bookkeeping.

        Arguments:
            time (float): Simulation time, in seconds.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
            allow_activation (bool): Allow the trinket to be activated
                automatically if the appropriate conditions are met. Defaults
                True, but can be set False if the user wants to control
                trinket activations manually.

        Returns:
            damage_done (float): Any instant damage that is dealt if the
                trinket is activated at the specified time. Defaults to 0 for
                standard trinkets, but custom subclasses can implement fixed
                damage procs that would be returned on each update.
        """
        # Update average proc uptime value
        if time > self.last_update:
            dt = time - self.last_update
            self.uptime = (
                (self.uptime * self.last_update + dt * self.active) / time
            )
            self.last_update = time

        # First check if an existing buff has fallen off
        if self.active and (time > self.deactivation_time - 1e-9):
            self.deactivate(player, sim)

        # Then check whether the trinket is off CD and can now proc
        if (not self.can_proc
                and (time - self.activation_time > self.cooldown - 1e-9)):
            self.can_proc = True

        # Now decide whether a proc actually happens
        if allow_activation and self.apply_proc():
            return self.activate(time, player, sim)

        # Return default damage dealt of 0
        return 0.0

    def apply_proc(self):
        """Determine whether or not the trinket is activated at the current
        time. This method must be implemented by Trinket subclasses.

        Returns:
            proc_applied (bool): Whether or not the activation occurs.
        """
        return NotImplementedError(
            'Logic for trinket activation must be implemented by Trinket '
            'subclasses.'
        )


class ActivatedTrinket(Trinket):
    """Models an on-use trinket that is activated on cooldown as often as
    possible."""

    def __init__(
        self, stat_name, stat_increment, proc_name, proc_duration, cooldown,
        delay=0.0
    ):
        """Initialize a generic activated trinket with key parameters.

        Arguments:
            stat_name (str): Name of the Player attribute that will be
                modified by the trinket activation. Must be a valid attribute
                of the Player class that can be modified. The one exception is
                haste_rating, which is separately handled by the Simulation
                object when updating timesteps for the sim.
            stat_increment (float): Amount by which the Player attribute is
                changed when the trinket is active.
            proc_name (str): Name of the buff that is applied when the trinket
                is active. Used for combat logging.
            proc_duration (int): Duration of the buff, in seconds.
            cooldown (int): Internal cooldown before the trinket can be
                activated again.
            delay (float): Optional time delay (in seconds) before the first
                trinket activation in the fight. Can be used to enforce a
                shared cooldown between two activated trinkets, or to delay the
                activation for armor debuffs etc. Defaults to 0.0 .
        """
        self.delay = delay
        Trinket.__init__(
            self, stat_name, stat_increment, proc_name, proc_duration,
            cooldown
        )

    def reset(self):
        """Set trinket to fresh inactive state at the start of a fight."""
        if self.delay:
            # We put in a hack to set the "activation time" such that the
            # trinket is ready after precisely the delay
            self.activation_time = self.delay - self.cooldown
        else:
            # Otherwise, the initial activation time is set infinitely in the
            # past so that the trinket is immediately ready for activation.
            self.activation_time = -np.inf

        self.active = False
        self.can_proc = not self.delay
        self.num_procs = 0
        self.uptime = 0.0
        self.last_update = 0.0

    def apply_proc(self):
        """Determine whether or not the trinket is activated at the current
        time.

        Returns:
            proc_applied (bool): Whether or not the activation occurs.
        """
        # Activated trinkets follow the simple logic of being used as soon as
        # they are available.
        if self.can_proc:
            return True
        return False


class HastePotion(ActivatedTrinket):
    """Haste pots can be easily modeled within the same trinket class structure
    without the need for custom code."""

    def __init__(self, delay=0.0):
        """Initialize object at the start of a fight.

        Arguments:
            delay (float): Minimum elapsed time in the fight before the potion
                can be used. Can be used to delay the potion activation for
                armor debuffs going up, etc. Defaults to 0.0
        """
        ActivatedTrinket.__init__(
            self, 'haste_rating', 400, 'Haste Potion', 15, 60, delay=delay
        )
        self.max_procs = 1 if delay > 1e-9 else 2 # 1 pot per combat in WotLK

    def apply_proc(self):
        """Determine whether or not the trinket is activated at the current
        time.

        Returns:
            proc_applied (bool): Whether or not the activation occurs.
        """
        # Adjust standard ActivatedTrinket logic to prevent multiple Haste
        # Potion activations once combat has commenced.
        if self.can_proc and (self.num_procs < self.max_procs):
            return True
        return False


class Bloodlust(ActivatedTrinket):
    """Similar to haste pots, the trinket framework works perfectly for Lust as
    well, just that the percentage haste buff is handled a bit differently."""

    def __init__(self, delay=0.0):
        """Initialize object at the start of a fight.

        Arguments:
            delay (float): Minimum elapsed time in the fight before Lust is
                used. Can be used to delay lusting for armor debuffs going up,
                etc. Defaults to 0.0
        """
        ActivatedTrinket.__init__(
            self, None, 0.0, 'Bloodlust', 40, 600, delay=delay
        )

    def modify_stat(self, time, player, sim, *args):
        """Change swing timer when Bloodlust is applied or falls off.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        old_multi = sim.haste_multiplier
        haste_rating = ccs.calc_haste_rating(
            sim.swing_timer, multiplier=old_multi, cat_form=player.cat_form
        )
        new_multi = old_multi/1.3 if self.active else old_multi*1.3
        new_swing_timer = ccs.calc_swing_timer(
            haste_rating, multiplier=new_multi, cat_form=player.cat_form
        )
        sim.update_swing_times(time, new_swing_timer)
        sim.haste_multiplier = new_multi


class ProcTrinket(Trinket):
    """Models a passive trinket with a specified proc chance on hit or crit."""

    def __init__(
        self, stat_name, stat_increment, proc_name, chance_on_hit,
        proc_duration, cooldown, chance_on_crit=0.0, yellow_chance_on_hit=None,
        mangle_only=False
    ):
        """Initialize a generic proc trinket with key parameters.

        Arguments:
            stat_name (str): Name of the Player attribute that will be
                modified by the trinket activation. Must be a valid attribute
                of the Player class that can be modified. The one exception is
                haste_rating, which is separately handled by the Simulation
                object when updating timesteps for the sim.
            stat_increment (float): Amount by which the Player attribute is
                changed when the trinket is active.
            proc_name (str): Name of the buff that is applied when the trinket
                is active. Used for combat logging.
            chance_on_hit (float): Probability of a proc on a successful normal
                hit, between 0 and 1.
            chance_on_crit (float): Probability of a proc on a critical strike,
                between 0 and 1. Defaults to 0.
            yellow_chance_on_hit (float): If supplied, use a separate proc rate
                for special abilities. In this case, chance_on_hit will be
                interpreted as the proc rate for white attacks. Used for ppm
                trinkets where white and yellow proc rates are normalized
                differently.
            mangle_only (bool): If True, then designate this trinket as being
                able to proc exclusively on the Mangle ability. Defaults False.
            proc_duration (int): Duration of the buff, in seconds.
            cooldown (int): Internal cooldown before the trinket can proc
                again.
        """
        Trinket.__init__(
            self, stat_name, stat_increment, proc_name, proc_duration,
            cooldown
        )

        if yellow_chance_on_hit is not None:
            self.rates = {
                'white': chance_on_hit, 'yellow': yellow_chance_on_hit
            }
            self.separate_yellow_procs = True
        else:
            self.chance_on_hit = chance_on_hit
            self.chance_on_crit = chance_on_crit
            self.separate_yellow_procs = False

        self.mangle_only = mangle_only

    def check_for_proc(self, crit, yellow):
        """Perform random roll for a trinket proc upon a successful attack.

        Arguments:
            crit (bool): Whether the attack was a critical strike.
            yellow (bool): Whether the attack was a special ability rather
                than a melee attack.
        """
        if not self.can_proc:
            self.proc_happened = False
            return

        proc_roll = np.random.rand()

        if self.separate_yellow_procs:
            rate = self.rates['yellow'] if yellow else self.rates['white']
        else:
            rate = self.chance_on_crit if crit else self.chance_on_hit

        if proc_roll < rate:
            self.proc_happened = True
        else:
            self.proc_happened = False

    def apply_proc(self):
        """Determine whether or not the trinket is activated at the current
        time. For a proc trinket, it is assumed that a check has already been
        made for the proc when the most recent attack occurred.

        Returns:
            proc_applied (bool): Whether or not the activation occurs.
        """
        if self.can_proc and self.proc_happened:
            self.proc_happened = False
            return True
        return False

    def reset(self):
        """Set trinket to fresh inactive state with no cooldown remaining."""
        Trinket.reset(self)
        self.proc_happened = False


class StackingProcTrinket(ProcTrinket):
    """Models trinkets that provide temporary stacking buffs to the player
    after an initial proc or activation."""

    def __init__(
        self, stat_name, stat_increment, max_stacks, aura_name, stack_name,
        chance_on_hit, yellow_chance_on_hit, aura_duration, cooldown,
        aura_type='activated', aura_proc_rates=None
    ):
        """Initialize a generic stacking proc trinket with key parameters.

        Arguments:
            stat_name (str): Name of the Player attribute that will be
                modified when stacks are accumulated. Must be a valid attribute
                of the Player class that can be modified. The one exception is
                haste_rating, which is separately handled by the Simulation
                object when updating timesteps for the sim.
            stat_increment (int): Amount by which the Player attribute is
                changed from one additional stack of the trinket buff.
            max_stacks (int): Maximum number of stacks that can be accumulated.
            aura_name (str): Name of the aura that is applied when the trinket
                is active, allowing for stack accumulation. Used for combat
                logging.
            stack_name (str): Name of the actual stacking buff that procs when
                the above aura is active.
            chance_on_hit (float): Probability of applying a new stack of the
                buff when the aura is active upon a successful normal hit,
                between 0 and 1.
            yellow_chance_on_hit (float): Same as above, but for special
                abilities.
            aura_duration (float): Duration of the trinket aura as well as any
                buff stacks that are accumulated when the aura is active.
            cooldown (float): Internal cooldown before the aura can be applied
                again once it falls off.
            aura_type (str): Either "activated" or "proc", specifying whether
                the overall stack accumulation aura is applied via player
                activation of the trinket or via another proc mechanic.
            aura_proc_rates (dict): Dictionary containing "white" and "yellow"
                keys specifying the chance on hit for activating the aura and
                enabling subsequent stack accumulation. Required and used only
                when aura_type is "proc".
        """
        self.stack_increment = stat_increment
        self.max_stacks = max_stacks
        self.aura_name = aura_name
        self.stack_name = stack_name
        self.stack_proc_rates = {
            'white': chance_on_hit, 'yellow': yellow_chance_on_hit
        }
        self.activated_aura = (aura_type == 'activated')
        self.aura_proc_rates = aura_proc_rates
        ProcTrinket.__init__(
            self, stat_name=stat_name, stat_increment=0, proc_name=aura_name,
            proc_duration=aura_duration, cooldown=cooldown,
            chance_on_hit=self.stack_proc_rates['white'],
            yellow_chance_on_hit=self.stack_proc_rates['yellow']
        )

    def reset(self):
        """Full reset of the trinket at the start of a fight."""
        self.activation_time = -np.inf
        self._reset()
        self.stat_increment = 0
        self.num_procs = 0
        self.uptime = 0.0
        self.last_update = 0.0

    def _reset(self):
        self.active = False
        self.can_proc = False
        self.proc_happened = False
        self.num_stacks = 0
        self.proc_name = self.aura_name

        if not self.activated_aura:
            self.rates = self.aura_proc_rates

    def deactivate(self, player, sim, time=None):
        """Deactivate the trinket buff when the duration has expired.

        Arguments:
            player (tbc_cat_sim.Player): Player object whose attributes will be
                restored to their original values.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
            time (float): Time at which the trinket is deactivated. Defaults to
                the stored time for automatic deactivation.
        """
        # Temporarily change the stat increment to the total value gained while
        # the trinket was active
        self.stat_increment = self.stack_increment * self.num_stacks

        # Reset trinket to inactive state
        self._reset()
        Trinket.deactivate(self, player, sim, time=time)
        self.stat_increment = 0

    def apply_proc(self):
        """Determine whether a new trinket activation takes place, or whether
        a new stack is applied to an existing activation."""
        # If can_proc is True but the stat increment is 0, it means that the
        # last event was a trinket deactivation, so we activate the trinket.
        if (self.activated_aura and (not self.active) and self.can_proc
                and (self.stat_increment == 0)):
            return True

        # Ignore procs when at max stacks, and prevent future proc checks
        if self.num_stacks == self.max_stacks:
            self.can_proc = False
            return False

        return ProcTrinket.apply_proc(self)

    def activate(self, time, player, sim):
        """Activate the trinket when off cooldown. If already active and a
        trinket proc just occurred, then add a new stack of the buff.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        if not self.active:
            # Activate the trinket on a fresh use
            Trinket.activate(self, time, player, sim)
            self.can_proc = True
            self.proc_name = self.stack_name
            self.stat_increment = self.stack_increment
            self.rates = self.stack_proc_rates
        else:
            # Apply a new buff stack. We do this "manually" rather than in the
            # parent method because a new stack doesn't count as an actual
            # activation.
            self.modify_stat(time, player, sim, self.stat_increment)
            self.num_stacks += 1

            # Log if requested
            if sim.log:
                sim.combat_log.append(
                    sim.gen_log(time, self.proc_name, 'applied')
                )

        return 0.0


class PoisonVial(ProcTrinket):
    """Custom class to handle instant damage procs from the Romulo's Poison
    Vial trinket."""

    def __init__(self, white_chance_on_hit, yellow_chance_on_hit, *args):
        """Initialize a Trinket object modeling RPV. Since RPV is a ppm
        trinket, the user must pre-calculate the proc chances based on the
        swing timer and equipped weapon speed.

        Arguments:
            white_chance_on_hit (float): Probability of a proc on a successful
                normal hit, between 0 and 1.
            yellow_chance_on_hit (float): Separate proc rate for special
                abilities.
        """
        ProcTrinket.__init__(
            self, stat_name=None, stat_increment=None,
            proc_name="Romulo's Poison", proc_duration=0,
            cooldown=0., chance_on_hit=white_chance_on_hit,
            yellow_chance_on_hit=yellow_chance_on_hit
        )

    def activate(self, time, sim):
        """Deal damage when the trinket procs.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.

        Returns:
            damage_done (float): Damage dealt by the proc.
        """
        self.num_procs += 1

        # First roll for miss. Assume 0 spell hit, so miss chance is 17%.
        miss_roll = np.random.rand()

        if miss_roll < 0.17:
            if sim.log:
                sim.combat_log.append(
                    sim.gen_log(time, self.proc_name, 'miss')
                )

            return 0.0

        # Now roll the base damage done by the proc
        base_damage = 222 + np.random.rand() * 110

        # Now roll for partial resists. Assume that the boss has no nature
        # resistance, so the only source of partials is the level based
        # resistance of 24 for a boss mob. The partial resist table for this
        # condition was taken from this calculator:
        # https://royalgiraffe.github.io/legacy-sim/#/resistances
        resist_roll = np.random.rand()

        if resist_roll < 0.84:
            dmg_done = base_damage
        elif resist_roll < 0.95:
            dmg_done = 0.75 * base_damage
        elif resist_roll < 0.99:
            dmg_done = 0.5 * base_damage
        else:
            dmg_done = 0.25 * base_damage

        if sim.log:
            sim.combat_log.append(
                sim.gen_log(time, self.proc_name, '%d' % dmg_done)
            )

        return dmg_done

    def update(self, time, player, sim):
        """Check if a trinket proc occurred on the player's last attack, and
        perform associated bookkeeping.

        Arguments:
            time (float): Simulation time, in seconds.
            player (tbc_cat_sim.Player): Player object whose attributes can be
                modified by trinket procs. Unused for RPV calculations, but
                required by the Trinket API.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.

        Returns:
            damage_done (float): Damage dealt by the trinket since the last
                check.
        """
        if self.apply_proc():
            return self.activate(time, sim)

        return 0.0


class RefreshingProcTrinket(ProcTrinket):
    """Handles trinkets that can proc when already active to refresh the buff
    duration."""

    def activate(self, time, player, sim):
        """Activate the trinket buff upon player usage or passive proc.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (tbc_cat_sim.Player): Player object whose attributes will be
                modified by the trinket proc.
            sim (tbc_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        # The only difference between a standard and repeating proc is that
        # we want to make sure that the buff doesn't stack and merely
        # refreshes. This is accomplished by deactivating the previous buff and
        # then re-applying it.
        if self.active:
            self.deactivate(player, sim, time=time)

        return ProcTrinket.activate(self, time, player, sim)


# Library of recognized TBC trinkets and associated parameters
trinket_library = {
    'brooch': {
        'type': 'activated',
        'passive_stats': {
            'attack_power': 72,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 278,
            'proc_name': 'Lust for Battle',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'berserkers_call': {
        'type': 'activated',
        'passive_stats': {
            'attack_power': 90,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 360,
            'proc_name': 'Call of the Berserker',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'slayers': {
        'type': 'activated',
        'passive_stats': {
            'attack_power': 64,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 260,
            'proc_name': "Slayer's Crest",
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'icon': {
        'type': 'activated',
        'passive_stats': {
            'hit_chance': 30./15.77/100,
        },
        'active_stats': {
            'stat_name': 'armor_pen_rating',
            'stat_increment': 85,
            'proc_name': 'Armor Penetration',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'abacus': {
        'type': 'activated',
        'passive_stats': {
            'attack_power': 64,
        },
        'active_stats': {
            'stat_name': 'haste_rating',
            'stat_increment': 260,
            'proc_name': 'Haste',
            'proc_duration': 10,
            'cooldown': 120,
        },
    },
    'kiss': {
        'type': 'activated',
        'passive_stats': {
            'crit_chance': 14./22.1/100,
            'hit_chance': 10./15.77/100,
        },
        'active_stats': {
            'stat_name': 'haste_rating',
            'stat_increment': 200,
            'proc_name': 'Kiss of the Spider',
            'proc_duration': 15,
            'cooldown': 120,
        },
    },
    'tenacity': {
        'type': 'activated',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'Agility',
            'stat_increment': 150,
            'proc_name': 'Heightened Reflexes',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'crystalforged': {
        'type': 'activated',
        'passive_stats': {
            'bonus_damage': 7,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 216,
            'proc_name': 'Valor',
            'proc_duration': 10,
            'cooldown': 60,
        },
    },
    'hourglass': {
        'type': 'proc',
        'passive_stats': {
            'crit_chance': 32./22.1/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 300,
            'proc_name': 'Rage of the Unraveller',
            'proc_duration': 10,
            'cooldown': 50,
            'proc_type': 'chance_on_crit',
            'proc_rate': 0.1,
        },
    },
    'tsunami': {
        'type': 'proc',
        'passive_stats': {
            'crit_chance': 38./22.1/100,
            'hit_chance': 10./15.77/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 340,
            'proc_name': 'Fury of the Crashing Waves',
            'proc_duration': 10,
            'cooldown': 45,
            'proc_type': 'chance_on_crit',
            'proc_rate': 0.1,
        },
    },
    'dst': {
        'type': 'proc',
        'passive_stats': {
            'attack_power': 40,
        },
        'active_stats': {
            'stat_name': 'haste_rating',
            'stat_increment': 325,
            'proc_name': 'Haste',
            'proc_duration': 10,
            'cooldown': 20,
            'proc_type': 'ppm',
            'proc_rate': 1.,
        },
    },
    'swarmguard': {
        'type': 'stacking_proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'armor_pen_rating',
            'stat_increment': 28,
            'max_stacks': 6,
            'aura_name': 'Badge of the Swarmguard',
            'stack_name': 'Insight of the Qiraji',
            'proc_type': 'ppm',
            'proc_rate': 10.,
            'aura_duration': 30,
            'cooldown': 180,
        },
    },
    'vial': {
        'type': 'proc',
        'passive_stats': {
            'hit_chance': 35./15.77/100,
        },
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'ppm',
            'proc_rate': 1.,
        },
    },
    'wildheart': {
        'type': 'refreshing_proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'Strength',
            'stat_increment': 64,
            'proc_name': 'Feline Blessing',
            'proc_duration': 15,
            'cooldown': 0,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.03,
        },
    },
    'ashtongue': {
        'type': 'refreshing_proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'Strength',
            'stat_increment': 140,
            'proc_name': 'Ashtongue Talisman of Equilibrium',
            'proc_duration': 8,
            'cooldown': 0,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.4,
            'mangle_only': True,
        },
    },
    'steely_naaru_sliver': {
        'type': 'passive',
        'passive_stats': {
            'expertise_rating': 54,
        },
    },
    'shard_of_contempt': {
        'type': 'proc',
        'passive_stats': {
            'expertise_rating': 44,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 230,
            'proc_name': 'Disdain',
            'proc_duration': 20,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.1,
        },
    },
    'madness': {
        'type': 'refreshing_proc',
        'passive_stats': {
            'hit_chance': 20./15.77/100,
            'attack_power': 84,
        },
        'active_stats': {
            'stat_name': 'armor_pen_rating',
            'stat_increment': 42,
            'proc_name': 'Forceful Strike',
            'proc_duration': 10,
            'cooldown': 0,
            'proc_type': 'ppm',
            'proc_rate': 1.,
        },
    },
    'motc': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 150,
        },
    },
    'dft': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 56,
            'hit_chance': 20./15.77/100,
        },
    },
    'alch': {
        'type': 'passive',
        'passive_stats': {
            'strength': 15,
            'agility': 15,
            'intellect': 15,
            'spirit': 15,
            'mana_pot_multi': 0.4,
        },
    },
    'assassin_alch': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 108,
            'mana_pot_multi': 0.4,
        },
    },
    'bns': {
        'type': 'stacking_proc',
        'passive_stats': {
            'haste_rating': 54,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 44,
            'max_stacks': 10,
            'aura_name': 'Battle Trance',
            'stack_name': 'Combat Insight',
            'proc_type': 'custom',
            'chance_on_hit': 1.0,
            'yellow_chance_on_hit': 1.0,
            'aura_duration': 20,
            'cooldown': 45,
            'aura_type': 'proc',
            'aura_proc_rates': {'white': 0.1, 'yellow': 0.1},
        },
    },
    'crusade': {
        'type': 'stacking_proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 6,
            'max_stacks': 20,
            'aura_name': 'Aura of the Crusade',
            'stack_name': 'Aura of the Crusader',
            'proc_type': 'custom',
            'chance_on_hit': 1.0,
            'yellow_chance_on_hit': 1.0,
            'aura_duration': 1e9,
            'cooldown': 1e9,
        },
    },
}
