"""Code for modeling non-static trinkets in feral DPS simulation."""

import numpy as np
import wotlk_cat_sim as ccs
import sim_utils


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
            self, 'haste_rating', 500, 'Speed', 15, 60, delay=delay
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
        not_bear = player.cat_form or sim.strategy['flowershift']
        haste_rating = sim_utils.calc_haste_rating(
            sim.swing_timer, multiplier=old_multi, cat_form=not_bear
        )
        multi_fac = 1./1.3 if self.active else 1.3
        new_multi = old_multi * multi_fac
        new_swing_timer = sim_utils.calc_swing_timer(
            haste_rating, multiplier=new_multi, cat_form=not_bear
        )
        sim.update_swing_times(time, new_swing_timer)
        sim.haste_multiplier = new_multi
        player.update_spell_gcd(
            haste_rating, multiplier=player.spell_haste_multiplier * multi_fac
        )


class UnholyFrenzy(ActivatedTrinket):
    """Models the external damage buff provided by Blood Death Knights."""

    def __init__(self, delay=0.0):
        """Initialize controller for Unholy Frenzy buff.

        Arguments:
            delay (float): Time delay, in seconds, before first buff
                application. Defaults to 0.
        """
        ActivatedTrinket.__init__(
            self, None, 0.0, 'Unholy Frenzy', 30, 180, delay=delay
        )

    def modify_stat(self, time, player, sim, *args):
        """Change global damage modifier when Unholy Frenzy is applied or falls
        off.

        Arguments:
            time (float): Simulation time, in seconds, of buff activation or
                deactivation.
            player (wotlk_cat_sim.Player): Player object whose attributes will
                be modified.
            sim (wotlk_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        damage_mod = 1./1.2 if self.active else 1.2
        player.damage_multiplier *= damage_mod
        player.calc_damage_params(**sim.params)


class ShatteringThrow(ActivatedTrinket):
    """Models the external armor penetration cooldown provided by Warriors."""

    def __init__(self, delay=0.0):
        """Inititalize controller for Shattering Throw debuff.

        Arguments:
            delay (float): Time delay, in seconds, before first usage. Defaults
                to 0.
        """
        ActivatedTrinket.__init__(
            self, None, 0.0, 'Shattering Throw', 10, 300, delay=delay
        )

    def modify_stat(self, time, player, sim, *args):
        """Change residual boss armor when Shattering Throw is applied or falls
        off.

        Arguments:
            time (float): Simulation time, in seconds.
            player (wotlk_cat_sim.Player): Player object whose damage values
                will be modified.
            sim (wotlk_cat_sim.Simulation): Simulation object controlling the
                fight execution.
        """
        sim.params['shattering_throw'] = not self.active
        player.calc_damage_params(**sim.params)


class ProcTrinket(Trinket):
    """Models a passive trinket with a specified proc chance on hit or crit."""

    def __init__(
        self, stat_name, stat_increment, proc_name, chance_on_hit,
        proc_duration, cooldown, chance_on_crit=0.0, yellow_chance_on_hit=None,
        mangle_only=False, cat_mangle_only=False, shred_only=False, periodic_only=False,
        icd_precombat=0.0
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
            proc_duration (int): Duration of the buff, in seconds.
            cooldown (int): Internal cooldown before the trinket can proc
                again.
            mangle_only (bool): If True, then designate this trinket as being
                able to proc exclusively on the Mangle ability. Defaults False.
            cat_mangle_only (bool): If True, then designate this trinket as being
                able to proc exclusively on the Cat Mangle ability. Defaults False.
            shred_only (bool): If True, then designate this trinket as being
                able to proc exclusively on the Shred ability. Defaults False.
            periodic_only (bool): If True, then designate this trinket as being
                able to proc exclusively on periodic damage. Defaults False.
            icd_precombat (float): Optional time (in seconds) of resetting the
                trinket's internal cooldown before the fight. For example, 
                equipping trinkets before pull. Defaults to 0.0.
        """
        self.icd_precombat = icd_precombat
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
        self.cat_mangle_only = cat_mangle_only
        self.shred_only = shred_only
        self.periodic_only = periodic_only
        self.special_proc_conditions = (
            mangle_only or cat_mangle_only or shred_only or periodic_only
        )

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
        self.can_proc = self.icd_precombat > self.cooldown - 1e-9 if self.icd_precombat else True
        self.activation_time = -self.icd_precombat if self.icd_precombat else -np.inf
        self.proc_happened = False


class IdolOfTheCorruptor(ProcTrinket):
    """Custom class to model the Mangle proc from Idol of the Corruptor, which
    has different proc rates in Cat Form vs. Dire Bear Form and which can be
    dynamically unequipped and re-equipped in combat as an advanced tactic."""

    def __init__(self, stat_mod, ap_mod):
        """Initialize Idol with default state set to "equipped" with Cat Form
        proc rate.

        Arguments:
            stat_mod (float): Multiplicative scaling factor for primary stats
                from talents and raid buffs.
            ap_mod (float): Multiplicative scaling factor for Attack Power in
                Cat Form from talents and raid buffs.
        """
        agi_gain = 162. * stat_mod
        ProcTrinket.__init__(
            self, ['agility', 'attack_power', 'crit_chance'],
            np.array([agi_gain, agi_gain * ap_mod, agi_gain / 83.33 / 100.]),
            'Primal Wrath', 1.0, 12, 0, mangle_only=True
        )
        self.equipped = True

    def update(self, time, player, sim):
        """Adjust Idol proc chance based on whether it is currently equipped
        and the player's current form, then call normal Trinket update loop.

        Arguments:
            time (float): Simulation time, in seconds.
            player (player.Player): Player object whose attributes will be
                modified by the Idol proc.
            sim (wotlk_cat_sim.Simulation): Simulation object controlling the
                fight execution.

        Returns:
            damage_done (float): Any instant damage that is dealt if the
                proc is activated at the specified time. Always 0 for Idol of
                the Corruptor.
        """
        if self.equipped:
            self.chance_on_hit = 1.0 if player.cat_form else 0.5
        else:
            self.chance_on_hit = 0.0

        return ProcTrinket.update(self, time, player, sim)


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


class InstantDamageProc(ProcTrinket):
    """Custom class to handle instant damage procs."""

    def __init__(
        self, proc_name, min_damage, damage_range, cooldown, chance_on_hit,
        chance_on_crit, icd_precombat=0.0, **kwargs
    ):
        """Initialize Trinket object.

        Arguments:
            proc_name (str): Name of the spell that is cast when the trinket
                procs. Used for combat logging.
            min_damage (float): Low roll damage of the proc, before partial
                resists.
            damage_range (float): Damage range of the proc.
            cooldown (int): Internal cooldown before the trinket can proc
                again.
            chance_on_hit (float): Probability of a proc on a successful normal
                hit, between 0 and 1.
            chance_on_crit (float): Probability of a proc on a critical strike,
                between 0 and 1.
        """
        ProcTrinket.__init__(
            self, stat_name='attack_power', stat_increment=0,
            proc_name=proc_name, proc_duration=0, cooldown=cooldown,
            chance_on_hit=chance_on_hit, chance_on_crit=chance_on_crit,
            periodic_only=kwargs.get('periodic_only', False), 
            icd_precombat=icd_precombat
        )
        self.min_damage = min_damage
        self.damage_range = damage_range

    def activate(self, time, player, sim):
        """Deal damage when the trinket procs.

        Arguments:
            time (float): Simulation time, in seconds, of activation.
            player (player.Player): Player object whose stats will be used for
                determining the proc damage.
            sim (wotlk_cat_sim.Simulation): Simulation object controlling the
                fight execution.

        Returns:
            damage_done (float): Damage dealt by the proc.
        """
        ProcTrinket.activate(self, time, player, sim)

        # First roll for miss. Infer spell miss chance from player's melee miss
        # chance. Assume Improved Faerie Fire / Misery debuffs.
        miss_chance = 0.14 - (
            (8. - (player.miss_chance - player.dodge_chance) * 100)
            * 32.79 / 26.23 / 100
        )
        miss_roll = np.random.rand()

        if miss_roll < miss_chance:
            if sim.log:
                sim.combat_log.append(
                    sim.gen_log(time, self.proc_name, 'miss')
                )

            return 0.0

        # Now roll the base damage done by the proc
        base_damage = self.min_damage + np.random.rand() * self.damage_range
        base_damage *= 1.03 * 1.13 # assume Santified Retribution / CoE

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


# Library of recognized trinkets and associated parameters
trinket_library = {
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
    'sphere': {
        'type': 'activated',
        'passive_stats': {
            'hit_chance': 55./32.79/100,
            'spell_hit_chance': 55./26.23/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 670,
            'proc_name': 'Heart of a Dragon',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'incisor_fragment': {
        'type': 'activated',
        'passive_stats': {
            'attack_power': 148,
        },
        'active_stats': {
            'stat_name': 'armor_pen_rating',
            'stat_increment': 291,
            'proc_name': 'Incisor Fragment',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'fezzik': {
        'type': 'activated',
        'passive_stats': {
            'haste_rating': 60,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 432,
            'proc_name': 'Argent Heroism',
            'proc_duration': 15,
            'cooldown': 120,
        },
    },
    'norgannon': {
        'type': 'activated',
        'passive_stats': {
            'expertise_rating': 69,
        },
        'active_stats': {
            'stat_name': 'haste_rating',
            'stat_increment': 491,
            'proc_name': 'Mark of Norgannon',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'loatheb': {
        'type': 'activated',
        'passive_stats': {
            'crit_chance': 84./45.91/100,
            'spell_crit_chance': 84./45.91/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 670,
            'proc_name': "Loatheb's Shadow",
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'whetstone': {
        'type': 'proc',
        'passive_stats': {
            'crit_chance': 74./45.91/100,
            'spell_crit_chance': 74./45.91/100,
        },
        'active_stats': {
            'stat_name': 'haste_rating',
            'stat_increment': 444,
            'proc_name': 'Meteorite Whetstone',
            'proc_duration': 10,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
        },
    },
    'mirror': {
        'type': 'proc',
        'passive_stats': {
            'crit_chance': 84./45.91/100,
            'spell_crit_chance': 84./45.91/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 1000,
            'proc_name': 'Reflection of Torment',
            'proc_duration': 10,
            'cooldown': 50,
            'proc_type': 'chance_on_crit',
            'proc_rate': 0.1,
        },
    },
    'tears': {
        'type': 'proc',
        'passive_stats': {
            'haste_rating': 73,
        },
        'active_stats': {
            'stat_name': 'haste_rating',
            'stat_increment': 410,
            'proc_name': 'Tears of Anguish',
            'proc_duration': 10,
            'cooldown': 50,
            'proc_type': 'chance_on_crit',
            'proc_rate': 0.1,
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
    'vestige': {
        'type': 'instant_damage',
        'passive_stats': {
            'haste_rating': 65,
        },
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
            'proc_name': 'Vestige of Haldor',
            'cooldown': 45,
            'min_damage': 1024,
            'damage_range': 512,
        },
    },
    'dmcd': {
        'type': 'instant_damage',
        'passive_stats': {
            'crit_chance': 85./45.91/100,
            'spell_crit_chance': 85./45.91/100,
        },
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
            'proc_name': 'Darkmoon Card: Death',
            'cooldown': 45,
            'min_damage': 1750,
            'damage_range': 500,
        },
    },
    'lightning_generator': {
        'type': 'instant_damage',
        'passive_stats': {
            'crit_chance': 84./45.91/100,
            'spell_crit_chance': 84./45.91/100,
        },
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'chance_on_hit',
            'proc_rate': 1.0,
            'proc_name': 'Gnomish Lightning Generator',
            'cooldown': 60,
            'min_damage': 1530,
            'damage_range': 340,
        },
    },
    'bandits_insignia': {
        'type': 'instant_damage',
        'passive_stats': {
            'attack_power': 190,
        },
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
            'proc_name': "Bandit's Insignia",
            'cooldown': 45,
            'min_damage': 1504,
            'damage_range': 752,
        },
    },
    'extract': {
        'type': 'instant_damage',
        'passive_stats': {
            'crit_chance': 95./45.91/100,
            'spell_crit_chance': 95./45.91/100,
        },
        'active_stats': {
            'stat_name': 'none',
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.1,
            'proc_name': 'Extract of Necromantic Power',
            'cooldown': 15,
            'min_damage': 788,
            'damage_range': 524,
            'periodic_only': True,
        },
    },
    'dmcg_str': {
        'type': 'proc',
        'passive_stats': {
            'strength': 90,
        },
        'active_stats': {
            'stat_name': 'Agility',
            'stat_increment': 300,
            'proc_name': 'Darkmoon Card: Greatness',
            'proc_duration': 15,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.35,
        },
    },
    'dmcg_agi': {
        'type': 'proc',
        'passive_stats': {
            'agility': 90,
        },
        'active_stats': {
            'stat_name': 'Agility',
            'stat_increment': 300,
            'proc_name': 'Darkmoon Card: Greatness',
            'proc_duration': 15,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.35,
        },
    },
    'grim_toll': {
        'type': 'proc',
        'passive_stats': {
            'hit_chance': 83./32.79/100,
            'spell_hit_chance': 83./26.23/100,
        },
        'active_stats': {
            'stat_name': 'armor_pen_rating',
            'stat_increment': 612,
            'proc_name': 'Grim Toll',
            'proc_duration': 10,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
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
            'hit_chance': 20./32.79/100,
            'spell_hit_chance': 20./26.23/100,
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
    'mighty_alch': {
        'type': 'passive',
        'passive_stats': {
            'attack_power': 100,
            'crit_chance': 50./45.91/100,
            'spell_crit_chance': 50./45.91/100,
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
    'fury_of_the_five_flights': {
        'type': 'stacking_proc',
        'passive_stats': {},
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 16,
            'max_stacks': 20,
            'aura_name': 'Fury of the Five Flights',
            'stack_name': 'Fury of the Five Flights',
            'proc_type': 'custom',
            'chance_on_hit': 1.0,
            'yellow_chance_on_hit': 1.0,
            'aura_duration': 1e9,
            'cooldown': 1e9,
        },
    },
    'comet_trail': {
        'type': 'proc',
        'passive_stats': {
            'attack_power': 271,
        },
        'active_stats': {
            'stat_name': 'haste_rating',
            'stat_increment': 819,
            'proc_name': 'Comet\'s Trail',
            'proc_duration': 10,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
        },
    },
    'dark_matter': {
        'type': 'proc',
        'passive_stats': {
            'attack_power': 251,
        },
        'active_stats': {
            'stat_name': ['crit_chance', 'spell_crit_chance'],
            'stat_increment': np.array([692./45.91/100, 692./45.91/100]),
            'proc_name': 'Dark Matter',
            'proc_duration': 10,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
        },
    },
    'mjolnir_runestone': {
        'type': 'proc',
        'passive_stats': {
            'crit_chance': 115./45.91/100,
            'spell_crit_chance': 115./45.91/100,
        },
        'active_stats': {
            'stat_name': 'armor_pen_rating',
            'stat_increment': 751,
            'proc_name': 'Mjolnir Runestone',
            'proc_duration': 10,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.15,
        },
    },
    'pyrite_infuser': {
        'type': 'proc',
        'passive_stats': {
            'hit_chance': 100./32.79/100,
            'spell_hit_chance': 100./26.23/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 1305,
            'proc_name': 'Pyrite Infusion',
            'proc_duration': 10,
            'cooldown': 50,
            'proc_type': 'chance_on_crit',
            'proc_rate': 0.1,
        },
    },
    'blood_of_the_old_god': {
        'type': 'proc',
        'passive_stats': {
            'hit_chance': 114./32.79/100,
            'spell_hit_chance': 114./26.23/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 1284,
            'proc_name': 'Blood of the Old God',
            'proc_duration': 10,
            'cooldown': 50,
            'proc_type': 'chance_on_crit',
            'proc_rate': 0.1,
        },
    },
    'wrathstone': {
        'type': 'activated',
        'passive_stats': {
            'crit_chance': 114./45.91/100,
            'spell_crit_chance': 114./45.91/100,
        },
        'active_stats': {
            'stat_name': 'attack_power',
            'stat_increment': 905,
            'proc_name': 'Wrathstone',
            'proc_duration': 20,
            'cooldown': 120,
        },
    },
    'deaths_verdict_heroic': {
        'type': 'proc',
        'passive_stats': {
            'attack_power': 288,
        },
        'active_stats': {
            'stat_name': 'Agility',
            'stat_increment': 510,
            'proc_name': 'Death\'s Verdict Heroic',
            'proc_duration': 15,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.35,
        },
    },
    'deaths_verdict_normal': {
        'type': 'proc',
        'passive_stats': {
            'attack_power': 256,
        },
        'active_stats': {
            'stat_name': 'Agility',
            'stat_increment': 450,
            'proc_name': 'Death\'s Verdict normal',
            'proc_duration': 15,
            'cooldown': 45,
            'proc_type': 'chance_on_hit',
            'proc_rate': 0.35,
        },
    },
}
