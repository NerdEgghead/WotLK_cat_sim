"""Code for simulating the classic WoW feral cat DPS rotation."""

import numpy as np
import copy
import collections
import urllib
import multiprocessing
import psutil


def calc_white_damage(
    low_end, high_end, miss_chance, crit_chance, meta=False,
    predatory_instincts=True
):
    """Execute single roll table for a melee white attack.

    Arguments:
        low_end (float): Low end base damage of the swing.
        high_end (float): High end base damage of the swing.
        miss_chance (float): Probability that the swing is avoided.
        crit_chance (float): Probability of a critical strike.
        meta (bool): Whether the Relentless Earthstorm Diamond meta-gem is
            used. Defaults False.
        predatory_instincts (bool): Whether to apply the critical damage
            modifier from the Predatory Instincts talent. Defaults True.

    Returns:
        damage_done (float): Damage done by the swing.
        miss (bool): True if the attack was avoided.
        crit (bool): True if the attack was a critical strike.
    """
    outcome_roll = np.random.rand()

    if outcome_roll < miss_chance:
        return 0.0, True, False

    base_dmg = low_end + np.random.rand() * (high_end - low_end)

    if outcome_roll < miss_chance + 0.24:
        glance_reduction = 0.15 + np.random.rand() * 0.2
        return (1.0 - glance_reduction) * base_dmg, False, False
    if outcome_roll < miss_chance + 0.24 + crit_chance:
        crit_multi = 2.2 if predatory_instincts else 2.0
        return crit_multi * (1 + meta * 0.03) * base_dmg, False, True
    return base_dmg, False, False


def calc_yellow_damage(
    low_end, high_end, miss_chance, crit_chance, meta=False,
    predatory_instincts=True
):
    """Execute 2-roll table for a melee spell.

    Arguments:
        low_end (float): Low end base damage of the ability.
        high_end (float): High end base damage of the ability.
        miss_chance (float): Probability that the ability is avoided.
        crit_chance (float): Probability of a critical strike.
        meta (bool): Whether the Relentless Earthstorm Diamond meta-gem is
            used. Defaults False.
        predatory_instincts (bool): Whether to apply the critical damage
            modifier from the Predatory Instincts talent. Defaults True.

    Returns:
        damage_done (float): Damage done by the ability.
        miss (bool): True if the attack was avoided.
        crit (bool): True if the attack was a critical strike.
    """
    miss_roll = np.random.rand()

    if miss_roll < miss_chance:
        return 0.0, True, False

    base_dmg = low_end + np.random.rand() * (high_end - low_end)
    crit_roll = np.random.rand()

    if crit_roll < crit_chance:
        crit_multi = 2.2 if predatory_instincts else 2.0
        return crit_multi * (1 + meta * 0.03) * base_dmg, False, True
    return base_dmg, False, False


def piecewise_eval(t_fine, times, values):
    """Evaluate a piecewise constant function on a finer time mesh.

    Arguments:
        t_fine (np.ndarray): Desired mesh for evaluation.
        times (np.ndarray): Breakpoints of piecewise function.
        values (np.ndarray): Function values at the breakpoints.

    Returns:
        y_fine (np.ndarray): Function evaluated on the desired mesh.
    """
    result = np.zeros_like(t_fine)

    for i in range(len(times) - 1):
        result += values[i] * ((t_fine >= times[i]) & (t_fine < times[i + 1]))

    result += values[-1] * (t_fine >= times[-1])

    return result


def calc_swing_timer(haste_rating, multiplier=1.0, cat_form=True):
    """Calculate swing timer given a total haste rating stat.

    Arguments:
        haste_rating (int): Player haste rating stat.
        multiplier (float): Overall haste multiplier from multiplicative haste
            buffs such as Bloodlust. Defaults to 1.
        cat_form (bool): If True, calculate Cat Form swing timer. If False,
            calculate Dire Bear Form swing timer. Defaults True.

    Returns:
        swing_timer (float): Hasted swing timer in seconds.
    """
    base_timer = 1.0 if cat_form else 2.5
    return base_timer / (multiplier * (1 + haste_rating / 1577))


def calc_haste_rating(swing_timer, multiplier=1.0, cat_form=True):
    """Calculate the haste rating that is consistent with a given swing timer.

    Arguments:
        swing_timer (float): Hasted swing timer in seconds.
        multiplier (float): Overall haste multiplier from multiplicative haste
            buffs such as Bloodlust. Defaults to 1.
        cat_form (bool): If True, assume swing timer is for Cat Form. If False,
            assume swing timer is for Dire Bear Form. Defaults True.

    Returns:
        haste_rating (float): Unrounded haste rating.
    """
    base_timer = 1.0 if cat_form else 2.5
    return 1577 * (base_timer / (swing_timer * multiplier) - 1)


def gen_import_link(
    stat_weights, EP_name='Simmed Weights', multiplier=1.133, epic_gems=False
):
    """Generate 80upgrades stat weight import link from calculated weights.

    Arguments:
        stat_weights (dict): Dictionary of weights generated by a Simulation
            object. Required keys are: "1% hit", "1% crit", "1% haste",
            and "1 Armor Pen".
        EP_name (str): Name for the EP set for auto-populating the 70upgrades
            import interface. Defaults to "Simmed Weights".
        multiplier (float): Scaling factor for raw primary stats. Defaults to
            1.133 assuming Blessing of Kings and Predatory Instincts.
        epic_gems (bool): Whether Epic quality gems (10 Agility) should be
            assumed for socket weight calculations. Defaults to False (Rare
            quality 8 Agi gems).

    Returns:
        import_link (str): Full URL for stat weight import into 80upgrades.
    """
    link = 'https://eightyupgrades.com/ep/import?name='

    # EP Name
    link += urllib.parse.quote(EP_name)

    # Attack Power and Strength
    link += '&31=1&33=1.2&4=%.3f' % (2 * multiplier)

    # Agility
    # agi_weight = multiplier * (1 + stat_weights['1% crit'] / 40)
    agi_weight = stat_weights['1 Agility']
    link += '&0=%.2f' % agi_weight

    # Hit Rating and Expertise Rating
    hit_weight = stat_weights['1% hit'] / 15.77
    link += '&35=%.2f&46=%.2f' % (hit_weight, hit_weight)

    # Critical Strike Rating
    link += '&41=%.2f' % (stat_weights['1% crit'] / 22.1)

    # Haste Rating
    link += '&43=%.2f' % (stat_weights['1% haste'] / 15.77)

    # Armor Penetration
    link += '&87=%.2f' % stat_weights['1 Armor Pen Rating']

    # Weapon Damage
    link += '&51=%.2f' % stat_weights['1 Weapon Damage']

    # Gems
    gem_agi = 10 if epic_gems else 8
    gem_weight = agi_weight * gem_agi
    link += '&74=%.2f&75=%.2f&76=%.2f' % (gem_weight, gem_weight, gem_weight)

    return link


class Player():

    """Stores damage parameters, energy, combo points, and cooldowns for a
    simulated player in a boss encounter. Executes events in the cat DPS
    rotation."""

    @property
    def hit_chance(self):
        return self._hit_chance

    @hit_chance.setter
    def hit_chance(self, value):
        self._hit_chance = value
        self.calc_miss_chance()

    @property
    def expertise_rating(self):
        return self._expertise_rating

    @expertise_rating.setter
    def expertise_rating(self, value):
        self._expertise_rating = value
        self.calc_miss_chance()

    # Implement all ability costs as read-only properties here.

    def __init__(
            self, attack_power, ap_mod, agility, hit_chance, expertise_rating,
            crit_chance, armor_pen_rating, swing_timer, mana, intellect,
            spirit, mp5, jow=False, pot=True, cheap_pot=False, rune=True,
            t4_bonus=False, t6_2p=False, t6_4p=False, wolfshead=True,
            meta=False, bonus_damage=0, shred_bonus=0, rip_bonus=0,
            debuff_ap=0, multiplier=1.1, omen=True, primal_gore=True,
            feral_aggression=0, savage_fury=2, furor=3, natural_shapeshifter=3,
            intensity=3, potp=2, improved_mangle=0, weapon_speed=3.0,
            proc_trinkets=[], log=False
    ):
        """Initialize player with key damage parameters.

        Arguments:
            attack_power (int): Fully raid buffed attack power in Cat Form.
            ap_mod (float): Total multiplier for Attack Power in Cat Form.
            agility (int): Fully raid buffed Agility attribute.
            hit_chance (float): Chance to hit as a fraction.
            expertise_rating (int): Player's Expertise Rating stat.
            crit_chance (float): Fully raid buffed crit chance as a fraction.
            armor_pen_rating (int): Armor penetration rating from gear. Boss
                armor debuffs are handled by Simulation objects as they are not
                properties of the player character.
            swing_timer (float): Melee swing timer in seconds, including haste
                effects such as MCP, Warchief's Blessing, and libram enchants.
            mana (int): Fully raid buffed mana.
            intellect (int): Fully raid buffed Intellect.
            spirit (int): Fully raid buffed Spirit.
            mp5 (int): Bonus mp5 from gear or buffs.
            jow (bool): Whether the player is receiving Judgment of Wisdom
                procs. Defaults False.
            pot (bool): Whether mana potions are used. Defaults True.
            cheap_pot (bool): Whether the budget Super Mana Potion is used
                instead of the optimal Fel Mana Potion. Defaults False.
            rune (bool): Whether Dark/Demonic Runes are used. Defaults True.
            t4_bonus (bool): Whether the 2-piece T4 set bonus is used. Defaults
                False.
            t6_2p (bool): Whether the 2-piece T6 set bonus is used. Defaults
                False.
            t6_4p (bool): Whether the 4-piece T6 set bonus is used. Defaults
                False.
            wolfshead (bool): Whether Wolfshead is worn. Defaults to True.
            meta (bool): Whether a Relentless Earthstorm Diamond meta gem is
                socketed. Defaults to False.
            bonus_damage (int): Bonus weapon damage from buffs such as Bogling
                Root or Dense Weightstone. Defaults to 0.
            shred_bonus (int): Bonus damage to Shred ability from Idols and set
                bonuses. Defaults to 0.
            rip_bonus (int): Bonus periodic damage to Rip (per CP) from Idols
                and set bonuses. Defaults to 0.
            debuff_ap (int): Bonus Attack Power from boss debuffs such as
                Improved Hunter's Mark or Expose Weakness. Treated differently
                from "normal" AP because it does not boost abilities with
                explicit AP scaling. Defaults to 0.
            multiplier (float): Overall damage multiplier from talents and
                buffs. Defaults to 1.1 (from 5/5 Naturalist).
            omen (bool): Whether Omen of Clarity is active. Defaults True.
            primal_gore (bool): Whether Primal Gore is talented. Defaults True.
            feral_aggression (int): Points taken in Feral Aggression talent.
                Defaults to 2.
            savage_fury (int): Points taken in Savage Fury talent. Defaults
                to 0.
            furor (int): Points taken in Furor talent. Default to 3.
            natural_shapeshifter (int): Points taken in Natural Shapeshifter
                talent. Defaults to 3.
            intensity (int): Points taken in Intensity talent. Defaults to 3.
            potp (int): Points taken in Protector of the Pack talent. Defaults
                to 2.
            improved_mangle (int): Points taken in Improved Mangle talent.
                Defaults to 0.
            weapon_speed (float): Equipped weapon speed, used for calculating
                Omen of Clarity proc rate. Defaults to 3.0.
            proc_trinkets (list of trinkets.ProcTrinket): If applicable, a list
                of ProcTrinket objects modeling each on-hit or on-crit trinket
                used by the player.
            log (bool): If True, maintain a log of the most recent event,
                formatted as a list of strings [event, outcome, energy, combo
                points]. Defaults False.
        """
        self.attack_power = attack_power
        self.debuff_ap = debuff_ap
        self.agility = agility
        self.ap_mod = ap_mod
        self.bear_ap_mod = ap_mod / 1.1 * (1 + 0.02 * potp)

        # Set internal hit and expertise values, and derive total miss chance.
        self._hit_chance = hit_chance
        self.expertise_rating = expertise_rating

        self.crit_chance = crit_chance - 0.048
        self.armor_pen_rating = armor_pen_rating
        self.swing_timer = swing_timer
        self.mana_pool = mana
        self.intellect = intellect
        self.spirit = spirit
        self.mp5 = mp5
        self.jow = jow
        self.pot = pot
        self.cheap_pot = cheap_pot
        self.mana_pot_multi = 1.0
        self.rune = rune
        self.t4_bonus = t4_bonus
        self.bonus_damage = bonus_damage
        self.shred_bonus = shred_bonus
        self.rip_bonus = rip_bonus
        self._mangle_cost = 40 - 5 * t6_2p - 2 * improved_mangle
        self.t6_bonus = t6_4p
        self.wolfshead = wolfshead
        self.meta = meta
        self.damage_multiplier = multiplier
        self.omen = omen
        self.primal_gore = primal_gore
        self.feral_aggression = feral_aggression
        self.savage_fury = savage_fury
        self.furor = furor
        self.natural_shapeshifter = natural_shapeshifter
        self.intensity = intensity
        self.weapon_speed = weapon_speed
        self.omen_rates = {
            'white': 3.5/60,
            'yellow': 0.0,
            'bear': 3.5/60*2.5,
        }
        self.proc_trinkets = proc_trinkets
        self.set_mana_regen()
        self.log = log
        self.reset()

    def calc_miss_chance(self):
        """Update overall miss chance when a change to the player's hit percent
        or Expertise Rating occurs."""
        miss_reduction = min(self._hit_chance * 100, 8.)
        dodge_reduction = min(
            6.5, (10 + np.floor(self._expertise_rating / 3.9425)) * 0.25
        )
        self.miss_chance = 0.01 * (
            (8. - miss_reduction) + (6.5 - dodge_reduction)
        )
        self.dodge_chance = 0.01 * (6.5 - dodge_reduction)

    def set_mana_regen(self):
        """Calculate and store mana regeneration rates based on specified regen
        stats.
        """
        # Mana regen is still linear in Spirit for TBC, but the scaling
        # coefficient is now Int-dependent.
        # 10/11/21 - Edited base_regen parameter from 0.009327 to 0.0085 while
        # shapeshifted, based on the average of three measurements by Rokpaus.
        # Neither number fits the caster/cat data exactly, so the formula is
        # likely not exact.
        self.regen_factor = 0.0085 * np.sqrt(self.intellect)
        base_regen = self.spirit * self.regen_factor
        bonus_regen = self.mp5 / 5

        # In TBC, the Intensity talent allows a portion of the base regen to
        # apply while within the five second rule
        self.regen_rates = {
            'base': base_regen + bonus_regen,
            'five_second_rule': 0.5/3*self.intensity*base_regen + bonus_regen,
        }
        self.shift_cost = 1224 * 0.4 * (1 - 0.1 * self.natural_shapeshifter)

        # Since Fel Mana pots regen over time rather than instantaneously, we
        # need to use a more sophisticated heuristic for when to pop it.
        # We pop the potion when our mana has decreased by 1.5x the value at
        # which we would be exactly topped off on average after the 24 second
        # pot duration, factoring in other regen sources and mana spent on
        # shifting. This provides buffer against rng with respect to JoW procs
        # or the number of shift cycles completed in that time.

        if self.cheap_pot:
            self.pot_threshold = self.mana_pool - 3000*self.mana_pot_multi
            return

        self.pot_threshold = self.mana_pool - 36 * (
            400./3 * self.mana_pot_multi
            + self.regen_rates['five_second_rule'] / 2
            + 35. * (1./self.swing_timer + 1./4.2)
        )

    def calc_damage_params(
            self, gift_of_arthas, boss_armor, sunder, faerie_fire,
            blood_frenzy, tigers_fury=False
    ):
        """Calculate high and low end damage of all abilities as a function of
        specified boss debuffs."""
        bonus_damage = (
            (self.attack_power + self.debuff_ap) / 14 + self.bonus_damage
            + 40 * tigers_fury
        )

        # Legacy compatibility with older Sunder code in case it is needed
        if isinstance(sunder, bool):
            sunder *= 5

        debuffed_armor = (
            boss_armor * (1 - 0.04 * sunder) * (1 - 0.05 * faerie_fire)
        )
        armor_constant = 467.5 * 70 - 22167.5
        arp_cap = (debuffed_armor + armor_constant) / 3.
        armor_pen = (
            self.armor_pen_rating / 6.73 / 100 * min(arp_cap, debuffed_armor)
        )
        residual_armor = debuffed_armor - armor_pen
        armor_multiplier = (
            1 - residual_armor / (residual_armor + armor_constant)
        )
        damage_multiplier = self.damage_multiplier * (1 + 0.04 * blood_frenzy)
        self.multiplier = armor_multiplier * damage_multiplier
        self.white_low = (43.0 + bonus_damage) * self.multiplier
        self.white_high = (66.0 + bonus_damage) * self.multiplier
        self.shred_low = 1.2 * (
            self.white_low * 2.25 + (405 + self.shred_bonus) * self.multiplier
        )
        self.shred_high = 1.2 * (
            self.white_high * 2.25 + (405 + self.shred_bonus) * self.multiplier
        )
        self.bite_multiplier = (
            self.multiplier * (1 + 0.03 * self.feral_aggression)
            * (1 + 0.15 * self.t6_bonus)
        )

        # Tooltip low range base values for Bite are 935 and 766, but that's
        # incorrect according to the DB.
        ap, bm = self.attack_power, self.bite_multiplier
        self.bite_low = {
            i: (169*i + 57 + 0.07 * i * ap) * bm for i in range(1, 6)
        }
        self.bite_high = {
            i: (169*i + 123 + 0.07 * i * ap) * bm for i in range(1, 6)
        }
        mangle_fac = 1 + 0.1 * self.savage_fury
        self.mangle_low = mangle_fac * (
            self.white_low * 2 + 330 * self.multiplier
        )
        self.mangle_high = mangle_fac * (
            self.white_high * 2 + 330 * self.multiplier
        )
        rake_multi = mangle_fac * damage_multiplier
        self.rake_hit = rake_multi * (90 + 0.01 * self.attack_power)
        self.rake_tick = rake_multi * (138 + 0.06 * self.attack_power)
        rip_multiplier = damage_multiplier * (1 + 0.15 * self.t6_bonus)
        self.rip_tick = {
            i: (24 + 47*i + 0.01*i*ap + self.rip_bonus*i) * rip_multiplier
            for i in range(1,6)
        }

        # Bearweave damage calculations
        bear_ap = self.bear_ap_mod * (
            self.attack_power / self.ap_mod - self.agility + 70
        )
        bear_bonus_damage = (
            (bear_ap + self.debuff_ap) / 14 * 2.5 + self.bonus_damage
        )
        self.white_bear_low = (109.0 + bear_bonus_damage) * self.multiplier
        self.white_bear_high = (165.0 + bear_bonus_damage) * self.multiplier
        self.maul_low = (self.white_bear_low + 290 * self.multiplier) * 1.872
        self.maul_high = (self.white_bear_high + 290 * self.multiplier) * 1.872
        self.mangle_bear_low = 1.2 * (
            self.white_bear_low * 1.15 + 155 * self.multiplier
        )
        self.mangle_bear_high = 1.2 * (
            self.white_bear_high * 1.15 + 155 * self.multiplier
        )
        self.lacerate_hit = (31 + 0.01 * bear_ap) * self.multiplier
        self.lacerate_tick = self.lacerate_hit / armor_multiplier # for 1 stack

        # Adjust damage values for Gift of Arthas
        if not gift_of_arthas:
            return

        for bound in ['low', 'high']:
            for ability in [
                'white', 'shred', 'mangle', 'white_bear', 'maul', 'mangle_bear'
            ]:
                attr = '%s_%s' % (ability, bound)
                setattr(self, attr, getattr(self, attr) + 8 * armor_multiplier)

            bite_damage = getattr(self, 'bite_%s' % bound)

            for cp in range(1, 6):
                bite_damage[cp] += 8 * armor_multiplier

    def reset(self):
        """Reset fight-specific parameters to their starting values at the
        beginning of an encounter."""
        self.gcd = 0.0
        self.omen_proc = False
        self.omen_icd = 0.0
        self.tf_cd = 0.0
        self.energy = 100
        self.combo_points = 0
        self.mana = self.mana_pool
        self.rage = 0
        self.rune_cd = 0.0
        self.pot_cd = 0.0
        self.pot_active = False
        self.innervated = False
        self.innervate_cd = 0.0
        self.five_second_rule = False
        self.cat_form = True
        self.t4_proc = False
        self.ready_to_shift = False
        self.berserk = False
        self.berserk_cd = 0.0
        self.enrage = False
        self.enrage_cd = 0.0
        self.mangle_cd = 0.0
        self.set_ability_costs()

        # Create dictionary to hold breakdown of total casts and damage
        self.dmg_breakdown = collections.OrderedDict()

        for cast_type in [
            'Melee', 'Mangle (Cat)', 'Shred', 'Rip', 'Rake', 'Ferocious Bite',
            'Shift (Bear)', 'Maul', 'Mangle (Bear)', 'Lacerate', 'Shift (Cat)'
        ]:
            self.dmg_breakdown[cast_type] = {'casts': 0, 'damage': 0.0}

    def set_ability_costs(self):
        """Store Energy costs for all specials in the rotation based on whether
        or not Berserk is active."""
        self.shred_cost = 42. / (1 + self.berserk)
        self.rake_cost = 35. / (1 + self.berserk)
        self.mangle_cost = self._mangle_cost / (1 + self.berserk)
        self.bite_cost = 35. / (1 + self.berserk)
        self.rip_cost = 30. / (1 + self.berserk)

    def check_omen_proc(self, yellow=False, spell=False):
        """Check for Omen of Clarity proc on a successful swing.

        Arguments:
            yellow (bool): Check proc for a yellow ability rather than a melee
                swing. Defaults False.
            spell (bool): Check proc for a spell cast rather than a melee
                swing or ability. Defaults False.
        """
        if (not self.omen) or yellow:
            return
        if spell and (self.omen_icd > 1e-9):
            return

        if spell:
            proc_rate = self.omen_rates['spell']
        elif self.cat_form:
            proc_rate = self.omen_rates['white']
        else:
            proc_rate = self.omen_rates['bear']

        proc_roll = np.random.rand()

        if proc_roll < proc_rate:
            self.omen_proc = True

            if spell:
                self.omen_icd = 10.0

    def check_jow_proc(self):
        """Check for a Judgment Wisdom on a successful melee attack."""
        if not self.jow:
            return

        proc_roll = np.random.rand()

        if proc_roll < 0.25:
            self.mana = min(self.mana + 70, self.mana_pool)

    def check_t4_proc(self):
        """Check for a 2p-T4 energy proc on a successful melee attack."""
        self.t4_proc = False

        if not self.t4_bonus:
            return

        proc_roll = np.random.rand()

        if proc_roll < 0.04:
            if self.cat_form:
                self.energy = min(self.energy + 20, 100)
            else:
                self.rage = min(self.rage + 10, 100)

            self.t4_proc = True

    def check_procs(self, yellow=False, crit=False):
        """Check all relevant procs that trigger on a successful attack.

        Arguments:
            yellow (bool): Check proc for a yellow ability rather than a melee
                swing. Defaults False.
            crit (bool): Whether the attack was a critical strike. Defaults
                False.
        """
        self.check_omen_proc(yellow=yellow)
        self.check_jow_proc()
        self.check_t4_proc()

        # Now check for all trinket procs that may occur. Only trinkets that
        # can trigger on all possible abilities will be checked here. The
        # handful of proc effects that trigger only on Mangle must be
        # separately checked within the mangle() function.
        for trinket in self.proc_trinkets:
            if not trinket.mangle_only:
                trinket.check_for_proc(crit, yellow)

    def regen(self, delta_t):
        """Update player Energy and Mana.

        Arguments:
            delta_t (float): Elapsed time, in seconds, since last resource
                update.
        """
        self.energy = min(100, self.energy + 10 * delta_t)

        if self.five_second_rule:
            mana_regen = self.regen_rates['five_second_rule']
        else:
            mana_regen = self.regen_rates['base']

        self.mana = min(self.mana + mana_regen * delta_t, self.mana_pool)

        if self.enrage:
            self.rage = min(100, self.rage + delta_t)

    def use_rune(self):
        """Pop a Dark/Demonic Rune to restore mana when appropriate.

        Returns:
            rune_used (bool): Whether the rune was used.
        """
        if ((not self.rune) or (self.rune_cd > 1e-9)
                or (self.mana > self.mana_pool - 1500)):
            return False

        self.mana += (900 + np.random.rand() * 600)
        self.rune_cd = 120.0
        return True

    def use_pot(self, time):
        """Pop a Mana Potion to restore mana when appropriate.

        Arguments:
            time (float): Time at which the potion is consumed. Used to
                generate a list of tick times for Fel Mana regen.

        Returns:
            pot_used (bool): Wheter the potion was used.
        """
        if ((not self.pot) or (self.pot_cd > 1e-9)
                or (self.mana > self.pot_threshold)):
            return False

        self.pot_cd = 120.0

        # If we're using cheap potions, we ignore the Fel Mana tick logic
        if self.cheap_pot:
            self.mana += (1800 + np.random.rand() * 1200)*self.mana_pot_multi
        else:
            self.pot_active = True
            self.pot_ticks = list(np.arange(time + 3, time + 24.01, 3))
            self.pot_end = time + 24

        return True

    def swing(self):
        """Execute a melee swing.

        Returns:
            damage_done (float): Damage done by the swing.
        """
        low = self.white_low if self.cat_form else self.white_bear_low
        high = self.white_high if self.cat_form else self.white_bear_high
        damage_done, miss, crit = calc_white_damage(
            low, high, self.miss_chance, self.crit_chance, meta=self.meta,
            predatory_instincts=self.cat_form
        )

        # Apply King of the Jungle for bear form swings
        if self.enrage:
            damage_done *= 1.15

        if not miss:
            # Check for Omen and JoW procs
            self.check_procs(crit=crit)

        # If in Dire Bear Form, generate Rage from the swing
        if not self.cat_form:
            # If the swing missed, then re-roll to see whether it was an actual
            # miss or a dodge, since dodges still generate Rage.
            dodge = False

            if miss:
                dodge = (np.random.rand() < self.dodge_chance/self.miss_chance)

            if dodge:
                # Determine how much damage a successful non-crit / non-glance
                # auto would have done.
                proxy_damage = 0.5 * (low + high) * (1 + 0.15 * self.enrage)
            else:
                proxy_damage = damage_done

            if (not miss) or dodge:
                rage_gen = (
                    15./4./274.7 * proxy_damage + 2.5/2*3.5 * (1 + crit)
                    + 5 * crit
                )
                self.rage = min(self.rage + rage_gen, 100)

        # Log the swing
        self.dmg_breakdown['Melee']['casts'] += 1
        self.dmg_breakdown['Melee']['damage'] += damage_done

        if self.log:
            self.gen_log('melee', damage_done, miss, crit, False)

        return damage_done

    def execute_bear_special(
        self, ability_name, min_dmg, max_dmg, rage_cost, yellow=True
    ):
        """Execute a special ability cast in Dire Bear form.

        Arguments:
            ability_name (str): Name of the ability for use in logging.
            min_dmg (float): Low end damage of the ability.
            max_dmg (float): High end damage of the ability.
            rage_cost (int): Rage cost of the ability.
            yellow (bool): Whether the ability should be treated as "yellow
                damage" for the purposes of proc calculations. Defaults True.

        Returns:
            damage_done (float): Damage done by the ability.
            success (bool): Whether the ability successfully landed.
        """
        # Perform Monte Carlo
        damage_done, miss, crit = calc_yellow_damage(
            min_dmg, max_dmg, self.miss_chance, self.crit_chance,
            meta=self.meta, predatory_instincts=False
        )

        # Apply King of the Jungle
        if self.enrage:
            damage_done *= 1.15

        # Set GCD
        if yellow:
            self.gcd = 1.5

        # Update Rage
        clearcast = self.omen_proc

        if clearcast:
            self.omen_proc = False
        else:
            self.rage -= rage_cost * (1 - 0.8 * miss)

        self.rage += 5 * crit

        # Check for procs
        if not miss:
            self.check_procs(crit=crit, yellow=yellow)

        # Log the cast
        self.dmg_breakdown[ability_name]['casts'] += 1
        self.dmg_breakdown[ability_name]['damage'] += damage_done

        if self.log:
            self.gen_log(ability_name, damage_done, miss, crit, clearcast)

        return damage_done, not miss

    def maul(self):
        """Execute a Maul when in Dire Bear Form.

        Returns:
            damage_done (float): Damage done by the Maul cast.
        """
        damage_done, success = self.execute_bear_special(
            'Maul', self.maul_low, self.maul_high, 10, yellow=False
        )
        return damage_done

    def gen_log(self, ability_name, dmg_done, miss, crit, clearcast):
        """Generate a combat log entry for an ability.

        Arguments:
            ability_name (str): Name of the ability.
            dmg_done (float): Damage done by the ability.
            miss (bool): Whether the ability missed.
            crit (bool): Whether the ability crit.
            clearcast (bool): Whether the ability was a Clearcast.
        """
        if miss:
            damage_str = 'miss' + ' (clearcast)' * clearcast
        else:
            try:
                damage_str = '%d' % dmg_done
            except TypeError:
                damage_str = dmg_done

            if crit and clearcast:
                damage_str += ' (crit, clearcast)'
            elif crit:
                damage_str += ' (crit)'
            elif clearcast:
                damage_str += ' (clearcast)'

            if self.t4_proc:
                if ')' in damage_str:
                    damage_str = damage_str[:-1] + ', T4 proc)'
                else:
                    damage_str += ' (T4 proc)'

        self.combat_log = [
            ability_name, damage_str, '%.1f' % self.energy,
            '%d' % self.combo_points, '%d' % self.mana, '%d' % self.rage
        ]

    def execute_builder(
        self, ability_name, min_dmg, max_dmg, energy_cost, mangle_mod=False
    ):
        """Execute a combo point builder (either Rake, Shred, or Mangle).

        Arguments:
            ability_name (str): Name of the ability for use in logging.
            min_dmg (float): Low end damage of the ability.
            max_dmg (float): High end damage of the ability.
            energy_cost (int): Energy cost of the ability.
            mangle_mod (bool): Whether to apply the Mangle damage modifier to
                the ability. Defaults False.

        Returns:
            damage_done (float): Damage done by the ability.
            success (bool): Whether the ability successfully landed.
        """
        # Perform Monte Carlo
        damage_done, miss, crit = calc_yellow_damage(
            min_dmg, max_dmg, self.miss_chance, self.crit_chance, self.meta
        )

        if mangle_mod:
            damage_done *= 1.3

        # Set GCD
        self.gcd = 1.0

        # Update energy
        clearcast = self.omen_proc

        if clearcast:
            self.omen_proc = False
        else:
            self.energy -= energy_cost * (1 - 0.8 * miss)

        # Update combo points
        points_added = 1 * (not miss) + crit
        self.combo_points = min(5, self.combo_points + points_added)

        # Check for Omen and JoW procs
        if not miss:
            self.check_procs(yellow=True, crit=crit)

        # Log the cast
        self.dmg_breakdown[ability_name]['casts'] += 1
        self.dmg_breakdown[ability_name]['damage'] += damage_done

        if self.log:
            self.gen_log(ability_name, damage_done, miss, crit, clearcast)

        return damage_done, not miss

    def shred(self):
        """Execute a Shred.

        Returns:
            damage_done (float): Damage done by the Shred cast.
            success (bool): Whether the Shred landed successfully.
        """
        damage_done, success = self.execute_builder(
            'Shred', self.shred_low, self.shred_high, self.shred_cost,
            mangle_mod=True
        )
        return damage_done, success

    def rake(self):
        """Execute a Rake.

        Returns:
            damage_done (float): Damage done by the Rake cast.
            success (bool): Whether the Rake landed successfully.
        """
        damage_done, success = self.execute_builder(
            'Rake', self.rake_hit, self.rake_hit, self.rake_cost,
            mangle_mod=True
        )
        return damage_done, success

    def lacerate(self):
        """Execute a Lacerate.

        Returns:
            damage_done (float): Damage done just by the Lacerate cast itself.
            success (bool): Whether the Lacerate debuff was successfully
                applied or refreshed.
        """
        return self.execute_bear_special(
            'Lacerate', self.lacerate_hit, self.lacerate_hit, 13
        )

    def mangle(self):
        """Execute a Mangle.

        Returns:
            damage_done (float): Damage done by the Mangle cast.
            success (bool): Whether the Mangle debuff was successfully applied.
        """
        if self.cat_form:
            dmg, success = self.execute_builder(
                'Mangle (Cat)', self.mangle_low, self.mangle_high,
                self.mangle_cost
            )
        else:
            dmg, success = self.execute_bear_special(
                'Mangle (Bear)', self.mangle_bear_low, self.mangle_bear_high,
                15
            )
            self.mangle_cd = 6.0

        # Since a handful of proc effects trigger only on Mangle, we separately
        # check for those procs here if the Mangle landed successfully.
        if success:
            for trinket in self.proc_trinkets:
                if trinket.mangle_only:
                    trinket.check_for_proc(False, True)

        return dmg, success

    def bite(self):
        """Execute a Ferocious Bite.

        Returns:
            damage_done (float): Damage done by the Bite cast.
        """
        # Bite always costs at least 35 combo points without Omen of Clarity
        clearcast = self.omen_proc

        if clearcast:
            self.omen_proc = False
        else:
            self.energy -= self.bite_cost

        # Update Bite damage based on excess energy available
        bonus_damage = (
            min(self.energy, 30) * (3.4 + self.attack_power / 410.)
            * self.bite_multiplier
        )

        # Perform Monte Carlo
        damage_done, miss, crit = calc_yellow_damage(
            self.bite_low[self.combo_points] + bonus_damage,
            self.bite_high[self.combo_points] + bonus_damage, self.miss_chance,
            self.crit_chance + 0.25, self.meta
        )

        # Consume energy pool and combo points on successful Bite
        if miss:
            self.energy += 0.8 * self.bite_cost * (not clearcast)
        else:
            self.energy -= min(self.energy, 30)
            self.combo_points = 0

        # Set GCD
        self.gcd = 1.0

        # Check for Omen and JoW procs
        if not miss:
            self.check_procs(yellow=True, crit=crit)

        # Log the cast
        self.dmg_breakdown['Ferocious Bite']['casts'] += 1
        self.dmg_breakdown['Ferocious Bite']['damage'] += damage_done

        if self.log:
            self.gen_log('Ferocious Bite', damage_done, miss, crit, clearcast)

        return damage_done

    def rip(self):
        """Cast Rip as a finishing move.

        Returns:
            damage_per_tick (float): Damage done per subsequent Rip tick.
            success (bool): Whether the Rip debuff was successfully applied.
        """
        # Perform Monte Carlo to see if it landed and record damage per tick
        miss = (np.random.rand() < self.miss_chance)
        damage_per_tick = self.rip_tick[self.combo_points] * (not miss)

        # Set GCD
        self.gcd = 1.0

        # Update energy
        clearcast = self.omen_proc

        if clearcast:
            self.omen_proc = False
        else:
            self.energy -= self.rip_cost * (1 - 0.8 * miss)

        # Consume combo points on successful cast
        self.combo_points *= miss

        # Check for Omen and JoW procs
        if not miss:
            self.check_procs(yellow=True)

        # Log the cast and total damage that will be done
        self.dmg_breakdown['Rip']['casts'] += 1
        self.dmg_breakdown['Rip']['damage'] += damage_per_tick * 8

        if self.log:
            self.gen_log('Rip', 'applied', miss, False, clearcast)

        return damage_per_tick, not miss

    def shift(self, time, powershift=False):
        """Execute a shift between Cat Form and Dire Bear Form.

        Arguments:
            time (float): Time at which the shift is executed, in seconds. Used
                for determining the five second rule.
            powershift (bool): If True, execute a powershift within the same
                form, rather than shifting between forms. Defaults False.
        """
        log_str = ''

        # Simple hack to accomodate same-form powershifts is to invert the
        # cat_form variable prior to executing a normal cat-to-bear shift.
        if powershift:
            self.cat_form = not self.cat_form

        if self.cat_form:
            self.cat_form = False
            self.rage = 10 * (np.random.rand() < 0.2 * self.furor)
            cast_name = 'Shift (Bear)'

            # Bundle Enrage with the bear shift if available
            if self.enrage_cd < 1e-9:
                self.rage += 20
                self.enrage = True
                self.enrage_cd = 60.
                log_str = 'use Enrage'
        else:
            self.cat_form = True
            self.energy = (
                min(self.energy, 20 * self.furor) + 20 * self.wolfshead
            )
            self.enrage = False
            cast_name = 'Shift (Cat)'

        self.gcd = 1.5
        self.dmg_breakdown[cast_name]['casts'] += 1
        self.mana -= self.shift_cost
        self.five_second_rule = True
        self.last_shift = time
        self.ready_to_shift = False

        # Pop a Dark Rune if we can get full value from it
        if self.use_rune():
            log_str = 'use Dark Rune'

        # Pop a Mana Potion if we can get full value from it
        if self.use_pot(time):
            log_str = 'use Mana Potion'

        if self.log:
            if powershift:
                cast_name = 'Powers' + cast_name[1:]

            self.combat_log = [
                cast_name, log_str, '%.1f' % self.energy,
                '%d' % self.combo_points, '%d' % self.mana, '%d' % self.rage
            ]

    def innervate(self, time):
        """Cast Innervate.

        Arguments:
            time (float): Time of Innervate cast, in seconds. Used for
                determining when the Innervate buff falls off.
        """
        self.mana -= 95  # Innervate mana cost
        self.innervate_end = time + 20
        self.innervated = True
        self.cat_form = False
        self.energy = 0
        self.gcd = 1.5
        self.innervate_cd = 360.0

        if self.log:
            self.combat_log = [
                'Innervate', '', '%d' % self.energy,
                '%d' % self.combo_points, '%d' % self.mana, '%d' % self.rage
            ]


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
            player (tbc_cat_sim.Player): Player object whose attributes will be
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


class Simulation():

    """Sets up and runs a simulated fight with the cat DPS rotation."""

    # Default fight parameters, including boss armor and all relevant debuffs.
    default_params = {
        'gift_of_arthas': True,
        'boss_armor': 3731,
        'sunder': False,
        'faerie_fire': True,
        'blood_frenzy': False
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
        'lacerate_prio': False,
        'lacerate_time': 10.0,
        'powerbear': False,
    }

    def __init__(
        self, player, fight_length, latency, trinkets=[], haste_multiplier=1.0,
        hot_uptime=0.0, **kwargs
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

        return damage_done

    def rake(self, time):
        """Instruct the Player to Rake, and perform related bookkeeping.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            damage_done (float): Damage done by the Rake initial hit.
        """
        damage_done, success = self.player.rake()

        # If it landed, flag the debuff as active and start timer
        if success:
            self.rake_debuff = True
            self.rake_end = time + 9.0
            self.rake_ticks = list(np.arange(time + 3, time + 9.01, 3))
            self.rake_damage = self.player.rake_tick

        return damage_done

    def lacerate(self, time):
        """Instruct the Player to Lacerate, and perform related bookkeeping.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            damage_done (float): Damage done by the Lacerate initial hit.
        """
        damage_done, success = self.player.lacerate()

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
                * (1 + 0.15 * self.player.enrage)
            )

        return damage_done * (1 + 0.3 * self.mangle_debuff)

    def rip(self, time):
        """Instruct Player to apply Rip, and perform related bookkeeping.

        Arguments:
            time (float): Current simulation time in seconds.
        """
        damage_per_tick, success = self.player.rip()

        if success:
            self.rip_debuff = True
            self.rip_start = time
            self.rip_end = time + 16.0
            self.rip_ticks = list(np.arange(time + 2, time + 16.01, 2))
            self.rip_damage = damage_per_tick

        return 0.0

    def shred(self):
        """Instruct Player to Shred, and perform related bookkeeping.

        Returns:
            damage_done (Float): Damage done by Shred cast.
        """
        damage_done, success = self.player.shred()

        # If it landed, apply Glyph of Shred
        if success and self.rip_debuff:
            if (self.rip_end - self.rip_start) < 22:
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
            return (self.rip_end - time >= self.strategy['bite_time'])
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
        # First calculate how much Energy we expect to accumulate before Rip
        # expires.
        # ripdur = self.rip_end - time
        ripdur = self.rip_start + 22 - time
        expected_energy_gain = 10 * ripdur

        if self.tf_expected_before(time, self.rip_end):
            expected_energy_gain += 60
        if self.player.omen:
            expected_energy_gain += ripdur / self.swing_timer * (
                3.5 / 60. * (1 - self.player.miss_chance) * 42
            )

        expected_energy_gain += ripdur / self.revitalize_frequency * 0.15 * 8
        total_energy_available = self.player.energy + expected_energy_gain

        # Now calculate the effective Energy cost for Biting now, which
        # includes the cost of the Ferocious Bite itself, the cost of building
        # 5 CPs for Rip, and the cost of Rip.
        ripcost, bitecost = self.get_finisher_costs(time)
        cp_per_builder = 1 + self.player.crit_chance
        # cost_per_builder = (42. + 42. + 35.) / 3. # ignore Berserk here
        cost_per_builder = (
            (42. + 42. + 35.) / 3. * (1 + 0.2 * self.player.miss_chance)
        )
        total_energy_cost = (
            bitecost + 5. / cp_per_builder * cost_per_builder + ripcost
        )

        # Actual Energy cost is a bit lower than this because it is okay to
        # lose a few seconds of Rip uptime to gain a Bite.
        allowed_rip_downtime = self.calc_allowed_rip_downtime(time)

        # Adjust downtime estimate to account for end of fight losses
        allowed_rip_downtime = 22. * (1 - 1. / (1. + allowed_rip_downtime/22.))

        total_energy_cost -= 10 * allowed_rip_downtime

        # Then we simply recommend Biting now if the available Energy to do so
        # exceeds the effective cost.
        return (total_energy_available > total_energy_cost)

    def get_finisher_costs(self, time):
        """Determine the expected Energy cost for Rip when it needs to be
        refreshed, and the expected Energy cost for Ferocious Bite if it is
        cast right now.

        Arguments:
            time (float): Current simulation time, in seconds.

        Returns:
            ripcost (float): Energy cost of future Rip refresh.
            bitecost (float): Energy cost of a current Ferocious Bite cast.
        """
        rip_end = time if (not self.rip_debuff) else self.rip_end
        ripcost = 15 if self.berserk_expected_at(time, rip_end) else 30

        if self.player.energy >= self.player.bite_cost:
            bitecost = min(self.player.bite_cost + 30, self.player.energy)
        else:
            bitecost = self.player.bite_cost + 10 * self.latency

        return ripcost, bitecost

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
        """
        rip_cp = self.strategy['min_combos_for_rip']
        bite_cp = self.strategy['min_combos_for_bite']
        rip_cost, bite_cost = self.get_finisher_costs(time)
        crit_factor = 2.2 * (1 + 0.03 * self.player.meta) - 1
        bite_base_dmg = 0.5 * (
            self.player.bite_low[bite_cp] + self.player.bite_high[bite_cp]
        )
        bite_bonus_dmg = (
            (bite_cost - self.player.bite_cost)
            * (3.4 + self.player.attack_power / 410.)
            * self.player.bite_multiplier
        )
        bite_dpc = (bite_base_dmg + bite_bonus_dmg) * (
            1 + crit_factor * (self.player.crit_chance + 0.25)
        )
        avg_rip_tick = self.player.rip_tick[rip_cp] * 1.3 * (
            1 + crit_factor * self.player.crit_chance * self.player.primal_gore
        )
        shred_dpc = (
            0.5 * (self.player.shred_low + self.player.shred_high) * 1.3
            * (1 + crit_factor * self.player.crit_chance)
        )
        # allowed_rip_downtime = bite_dpc / avg_rip_tick * 2
        allowed_rip_downtime = (
            (bite_dpc - (bite_cost - rip_cost) * shred_dpc / 42.)
            / avg_rip_tick * 2
        )
        return allowed_rip_downtime

    def execute_rotation(self, time):
        """Execute the next player action in the DPS rotation according to the
        specified player strategy in the simulation.

        Arguments:
            time (float): Current simulation time in seconds.

        Returns:
            damage_done (float): Damage done by the player action.
        """
        # If we're out of form because we just cast GotW/etc., always shift
        #if not self.player.cat_form:
        #    self.player.shift(time)
        #    return 0.0

        # If we previously decided to shift, then execute the shift now once
        # the input delay is over.
        if self.player.ready_to_shift:
            self.player.shift(time)

            if (self.player.mana < 0) and (not self.time_to_oom):
                self.time_to_oom = time

            # Swing timer only updates on the next swing after we shift
            swing_fac = 1/2.5 if self.player.cat_form else 2.5
            self.update_swing_times(
                self.swing_times[0], self.swing_timer * swing_fac,
                first_swing=True
            )
            return 0.0

        energy, cp = self.player.energy, self.player.combo_points
        rip_cp = self.strategy['min_combos_for_rip']
        bite_cp = self.strategy['min_combos_for_bite']

        # 10/6/21 - Added logic to not cast Rip if we're near the end of the
        # fight.
        end_thresh = 10
        # end_thresh = self.calc_allowed_rip_downtime(time)
        rip_now = (
            (cp >= rip_cp) and (not self.rip_debuff)
            and (self.fight_length - time >= end_thresh)
            and (not self.player.omen_proc)
        )
        bite_at_end = (
            (cp >= bite_cp)
            and ((self.fight_length - time < end_thresh) or (
                    self.rip_debuff and
                    (self.fight_length - self.rip_end < end_thresh)
                )
            )
        )

        mangle_now = (
            (not rip_now) and (not self.mangle_debuff)
            and (not self.player.omen_proc)
        )
        mangle_cost = self.player.mangle_cost

        bite_before_rip = (
            (cp >= bite_cp) and self.rip_debuff and self.strategy['use_bite']
            and self.can_bite(time)
        )
        bite_now = (
            (bite_before_rip or bite_at_end)
            and (not self.player.omen_proc)
        )

        # During Berserk, we additionally add an Energy constraint on Bite
        # usage to maximize the total Energy expenditure we can get.
        if bite_now and self.player.berserk:
            bite_now = (energy <= self.strategy['berserk_bite_thresh'])

        rake_now = (
            (self.strategy['use_rake']) and (not self.rake_debuff)
            and (self.fight_length - time > 9)
            and (not self.player.omen_proc)
        )

        berserk_energy_thresh = 90 - 10 * self.player.omen_proc
        berserk_now = (
            self.strategy['use_berserk'] and (self.player.berserk_cd < 1e-9)
            and (self.player.tf_cd > 15)
            and (energy < berserk_energy_thresh + 1e-9)
        )

        # First figure out how much Energy we must float in order to be able
        # to refresh our buffs/debuffs as soon as they fall off
        pending_actions = []
        rip_refresh_pending = False
        float_energy_for_rip = False

        if self.rip_debuff and (self.rip_end < self.fight_length - end_thresh):
            if self.berserk_expected_at(time, self.rip_end):
                rip_cost = 15
            else:
                rip_cost = 30

            pending_actions.append((self.rip_end, rip_cost))
            rip_refresh_pending = True

            # Separate floating Energy calculation for Rip, since only Rip
            # matters for determining Bite usage
            if self.rip_end - time < rip_cost / 10.:
                float_energy_for_rip = True
                #if not self.tf_expected_before(time, self.rip_end):
                    #float_energy_for_rip = True
        if self.rake_debuff and (self.rake_end < self.fight_length - 9):
            if self.berserk_expected_at(time, self.rake_end):
                pending_actions.append((self.rake_end, 17.5))
            else:
                pending_actions.append((self.rake_end, 35))
        if self.mangle_debuff and (self.mangle_end < self.fight_length - 1):
            base_cost = self.player._mangle_cost
            if self.berserk_expected_at(time, self.mangle_end):
                pending_actions.append((self.mangle_end, 0.5 * base_cost))
            else:
                pending_actions.append((self.mangle_end, base_cost))

        pending_actions.sort()

        # Allow for bearweaving if the next pending action is >= 4.5s away
        furor_cap = min(20 * self.player.furor, 85)
        # weave_energy = min(furor_cap - 30 - 20 * self.latency, 42)
        weave_energy = furor_cap - 30 - 20 * self.latency

        if self.player.furor > 3:
            weave_energy -= 15

        weave_end = time + 4.5 + 2 * self.latency
        bearweave_now = (
            self.strategy['bearweave'] and (energy <= weave_energy)
            and (not self.player.omen_proc) and
            # ((not pending_actions) or (pending_actions[0][0] >= weave_end))
            ((not rip_refresh_pending) or (self.rip_end >= weave_end))
            and (not self.tf_expected_before(time, weave_end))
            # and (not self.params['tigers_fury'])
            and (not self.player.berserk)
        )

        # If we're maintaining Lacerate, then allow for emergency bearweaves
        # if Lacerate is about to fall off even if the above conditions do not
        # apply.
        emergency_bearweave = (
            self.strategy['bearweave'] and self.strategy['lacerate_prio']
            and self.lacerate_debuff
            and (self.lacerate_end - time < 2.5 + self.latency)
        )

        floating_energy = 0
        previous_time = time
        #tf_pending = False

        for refresh_time, refresh_cost in pending_actions:
            delta_t = refresh_time - previous_time

            # if (not tf_pending):
            #     tf_pending = self.tf_expected_before(time, refresh_time)

            #     if tf_pending:
            #         refresh_cost -= 60

            if delta_t < refresh_cost / 10.:
                floating_energy += refresh_cost - 10 * delta_t
                previous_time = refresh_time
            else:
                previous_time += refresh_cost / 10.

        excess_e = energy - floating_energy
        time_to_next_action = 0.0

        if not self.player.cat_form:
            # Shift back into Cat Form if (a) our first bear auto procced
            # Clearcasting, or (b) our first bear auto didn't generate enough
            # Rage to Mangle or Maul, or (c) we don't have enough time or
            # Energy leeway to spend an additional GCD in Dire Bear Form.
            shift_now = (
                (energy + 15 + 10 * self.latency > furor_cap)
                or (rip_refresh_pending and (self.rip_end < time + 3.0))
            )

            if self.strategy['powerbear']:
                powerbear_now = (not shift_now) and (self.player.rage < 10)
            else:
                powerbear_now = False
                shift_now = shift_now or (self.player.rage < 10)

            if not self.strategy['lacerate_prio']:
                shift_now = shift_now or self.player.omen_proc

            lacerate_now = self.strategy['lacerate_prio'] and (
                (not self.lacerate_debuff) or (self.lacerate_stacks < 5)
                or (self.lacerate_end - time <= self.strategy['lacerate_time'])
            )
            emergency_lacerate = (
                self.strategy['lacerate_prio'] and self.lacerate_debuff
                and (self.lacerate_end - time < 3.0 + 2 * self.latency)
            )

            if emergency_lacerate and (self.player.rage >= 13):
                return self.lacerate(time)
            elif shift_now:
                self.player.ready_to_shift = True
            elif powerbear_now:
                self.player.shift(time, powershift=True)
            elif lacerate_now and (self.player.rage >= 13):
                return self.lacerate(time)
            elif (self.player.rage >= 15) and (self.player.mangle_cd < 1e-9):
                return self.mangle(time)
            elif self.player.rage >= 13:
                return self.lacerate(time)
            else:
                time_to_next_action = self.swing_times[0] - time
        elif emergency_bearweave:
            self.player.ready_to_shift = True
        elif berserk_now:
            self.apply_berserk(time)
            return 0.0
        elif rip_now:
            if (energy >= self.player.rip_cost) or self.player.omen_proc:
                return self.rip(time)
            time_to_next_action = (self.player.rip_cost - energy) / 10.
        elif bite_now and (not float_energy_for_rip):
            if energy >= self.player.bite_cost:
                return self.player.bite()
            time_to_next_action = (self.player.bite_cost - energy) / 10.
        elif mangle_now:
            if (energy >= mangle_cost) or self.player.omen_proc:
                return self.mangle(time)
            time_to_next_action = (mangle_cost - energy) / 10.
        elif rake_now:
            if (energy >= self.player.rake_cost) or self.player.omen_proc:
                return self.rake(time)
            time_to_next_action = (self.player.rake_cost - energy) / 10.
        elif bearweave_now:
            self.player.ready_to_shift = True
        elif self.strategy['mangle_spam'] and (not self.player.omen_proc):
            if excess_e >= mangle_cost:
                return self.mangle(time)
            time_to_next_action = (mangle_cost - excess_e) / 10.
        else:
            if (excess_e >= self.player.shred_cost) or self.player.omen_proc:
                return self.shred()
            time_to_next_action = (self.player.shred_cost - excess_e) / 10.

        # Model in latency when waiting on Energy for our next action
        next_action = time + time_to_next_action

        if pending_actions:
            next_action = min(next_action, pending_actions[0][0])

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

    def apply_haste_buff(self, time, haste_rating_increment):
        """Perform associated bookkeeping when the player Haste Rating is
        modified.

        Arguments:
            time (float): Simulation time in seconds.
            haste_rating_increment (int): Amount by which the player Haste
                Rating changes.
        """
        new_swing_timer = calc_swing_timer(
            calc_haste_rating(
                self.swing_timer, multiplier=self.haste_multiplier,
                cat_form=self.player.cat_form
            ) + haste_rating_increment,
            multiplier=self.haste_multiplier, cat_form=self.player.cat_form
        )
        self.update_swing_times(time, new_swing_timer)

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
        self.berserk_end = time + 15.
        self.player.berserk_cd = 180. - prepop

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
        self.innervate_threshold = self.player.shift_cost + 2237 #GotW cost
        self.mangle_debuff = False
        self.rip_debuff = False
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

        # Create placeholder for time to OOM if the player goes OOM in the run
        self.time_to_oom = None

        # Create empty lists of output variables
        times = []
        damage = []
        energy = []
        combos = []

        # The "damage_done" for Rip that is logged by the Player object is not
        # accurate to a given run, as it does not incorporate the Mangle
        # debuff or partial Rip ticks at the end. So we'll keep track of it
        # ourselves.
        rip_damage = 0.0

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
            self.player.omen_icd = max(0.0, self.player.omen_icd - delta_t)
            self.player.rune_cd = max(0.0, self.player.rune_cd - delta_t)
            self.player.pot_cd = max(0.0, self.player.pot_cd - delta_t)
            self.player.innervate_cd = max(
                0.0, self.player.innervate_cd - delta_t
            )
            self.player.tf_cd = max(0.0, self.player.tf_cd - delta_t)
            self.player.berserk_cd = max(0.0, self.player.berserk_cd - delta_t)
            self.player.enrage_cd = max(0.0, self.player.enrage_cd - delta_t)
            self.player.mangle_cd = max(0.0, self.player.mangle_cd - delta_t)

            if (self.player.five_second_rule
                    and (time - self.player.last_shift >= 5)):
                self.player.five_second_rule = False

            # Check if Innervate fell off
            if self.player.innervated and (time >= self.player.innervate_end):
                self.player.innervated = False
                self.player.regen_rates['base'] -= 2.25*3496/10
                self.player.regen_rates['five_second_rule'] -= 2.25*3496/10

                if self.log:
                    self.combat_log.append(self.gen_log(
                        self.player.innervate_end, 'Innervate', 'falls off'
                    ))

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

            # Check if a Rip tick happens at this time
            if self.rip_debuff and (time >= self.rip_ticks[0]):
                tick_damage = self.rip_damage * (1 + 0.3 * self.mangle_debuff)

                if self.player.primal_gore:
                    tick_damage, _, _ = calc_yellow_damage(
                        tick_damage, tick_damage, 0.0, self.player.crit_chance,
                        meta=self.player.meta,
                        predatory_instincts=self.player.cat_form
                    )

                dmg_done += tick_damage
                rip_damage += tick_damage
                self.rip_ticks.pop(0)

                if self.log:
                    self.combat_log.append(
                        self.gen_log(time, 'Rip tick', '%d' % tick_damage)
                    )

            # Check if Rip fell off
            if self.rip_debuff and (time > self.rip_end - 1e-9):
                self.rip_debuff = False

                if self.log:
                    self.combat_log.append(
                        self.gen_log(self.rip_end, 'Rip', 'falls off')
                    )

            # Check if a Rake tick happens at this time
            if self.rake_debuff and (time >= self.rake_ticks[0]):
                tick_damage = self.rake_damage * (1 + 0.3 * self.mangle_debuff)
                dmg_done += tick_damage
                self.player.dmg_breakdown['Rake']['damage'] += tick_damage
                self.rake_ticks.pop(0)

                if self.log:
                    self.combat_log.append(
                        self.gen_log(time, 'Rake tick', '%d' % tick_damage)
                    )

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
                tick_damage = self.lacerate_damage * (1+0.3*self.mangle_debuff)

                if self.player.primal_gore:
                    tick_damage, _, _ = calc_yellow_damage(
                        tick_damage, tick_damage, 0.0, self.player.crit_chance,
                        meta=self.player.meta,
                        predatory_instincts=self.player.cat_form
                    )

                dmg_done += tick_damage
                self.player.dmg_breakdown['Lacerate']['damage'] += tick_damage
                self.lacerate_ticks.pop(0)

                if self.log:
                    self.combat_log.append(
                        self.gen_log(time, 'Lacerate tick', '%d' % tick_damage)
                    )

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
            if ((not self.player.cat_form) and (self.player.enrage_cd < 1e-9)
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
                if self.player.cat_form:
                    dmg_done += self.player.swing()
                else:
                    # If we will have enough time and Energy leeway to stay in
                    # Dire Bear Form once the GCD expires, then only Maul if we
                    # will be left with enough Rage to cast Mangle or Lacerate
                    # on that global.
                    furor_cap = min(20 * self.player.furor, 85)
                    rip_refresh_pending = (
                        self.rip_debuff
                        and (self.rip_end < self.fight_length - 10)
                    )
                    energy_leeway = (
                        furor_cap - 15
                        - 10 * (self.player.gcd + self.latency)
                    )
                    shift_next = (self.player.energy > energy_leeway)

                    if rip_refresh_pending:
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

                    if self.player.rage >= maul_rage_thresh:
                        dmg_done += self.player.maul()
                    else:
                        dmg_done += self.player.swing()

                self.swing_times.pop(0)

                if self.log:
                    self.combat_log.append(
                        ['%.3f' % time] + self.player.combat_log
                    )

            # Check if a Fel Mana Potion tick happens at this time
            if self.player.pot_active and (time == self.player.pot_ticks[0]):
                mana_regen = 400 * self.player.mana_pot_multi
                self.player.mana = min(
                    self.player.mana + mana_regen, self.player.mana_pool
                )
                self.player.pot_ticks.pop(0)

                if self.log:
                    self.combat_log.append(
                        self.gen_log(time, 'Fel Mana tick', '')
                    )

            # Check if Fel Mana Potion expired
            if self.player.pot_active and (time > self.player.pot_end - 1e-9):
                self.player.pot_active = False

                if self.log:
                    self.combat_log.append(self.gen_log(
                        self.player.pot_end, 'Fel Mana', 'falls off'
                    ))

            # Check if we're able to act, and if so execute the optimal cast.
            self.player.combat_log = None

            if (self.player.gcd < 1e-9) and (time >= self.next_action):
                dmg_done += self.execute_rotation(time)

            # Append player's log to running combat log
            if self.log and self.player.combat_log:
                self.combat_log.append(
                    ['%.3f' % time] + self.player.combat_log
                )

            # If we entered caster form, Tiger's Fury fell off
            if self.params['tigers_fury'] and (self.player.gcd == 1.5):
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
                and self.player.cat_form
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
            if self.player.pot_active:
                time = min(time, self.player.pot_ticks[0])
            if self.proc_end_times:
                time = min(time, self.proc_end_times[0])

        # Replace logged Rip damgae with the actual value realized in the run
        self.player.dmg_breakdown['Rip']['damage'] = rip_damage

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

    def calc_deriv(self, num_replicates, param, increment, base_dps):
        """Calculate DPS increase after incrementing a player stat.

        Arguments:
            num_replicates (int): Number of replicates to run.
            param (str): Player attribute to increment.
            increment (float): Magnitude of stat increment.
            base_dps (float): Pre-calculated base DPS before stat increments.

        Returns:
            dps_delta (float): Average DPS increase after the stat increment.
                The Player attribute will be reset to its original value once
                the calculation is finished.
        """
        # Increment the stat
        original_value = getattr(self.player, param)
        setattr(self.player, param, original_value + increment)

        # For Agility increments, also augment Attack Power and Crit
        if param == 'agility':
            self.player.attack_power += self.player.ap_mod * increment
            self.player.crit_chance += increment / 40. / 100.

        # Calculate DPS
        dps_vals = self.run_replicates(num_replicates)
        avg_dps = np.mean(dps_vals)

        # Reset the stat to original value
        setattr(self.player, param, original_value)

        if param == 'agility':
            self.player.attack_power -= self.player.ap_mod * increment
            self.player.crit_chance -= increment / 40. / 100.

        return avg_dps - base_dps

    def calc_stat_weights(
            self, num_replicates, base_dps=None, agi_mod=1.0
    ):
        """Calculate performance derivatives for AP, hit, crit, and haste.

        Arguments:
            num_replicates (int): Number of replicates to run.
            base_dps (float): If provided, use a pre-calculated value for the
                base DPS before stat increments. Defaults to calculating base
                DPS from scratch.
            agi_mod (float): Multiplier for primary attributes to use for
                determining Agility weight. Defaults to 1.0

        Returns:
            dps_deltas (dict): Dictionary containing DPS increase from 1 AP,
                1% hit, 1% crit, and 1% haste.
            stat_weights (dict): Dictionary containing normalized stat weights
                for 1% hit, 1% crit, and 1% haste relative to 1 AP.
        """
        # First store base DPS and deltas after each stat increment
        dps_deltas = {}

        if base_dps is None:
            dps_vals = self.run_replicates(num_replicates)
            base_dps = np.mean(dps_vals)

        # For all stats, we will use a much larger increment than +1 in order
        # to see sufficient DPS increases above the simulation noise. We will
        # then linearize the increase down to a +1 increment for weight
        # calculation. This approximation is accurate as long as DPS is linear
        # in each stat up to the larger increment that was used.

        # For AP, we will use an increment of +80 AP. We also scale the
        # increase by a factor of 1.1 to account for HotW
        dps_deltas['1 AP'] = 1.0/80.0 * self.calc_deriv(
            num_replicates, 'attack_power', 80 * self.player.ap_mod, base_dps
        )

        # For hit and crit, we will use an increment of 2%.

        # For hit, we reduce miss chance by 2% if well below hit cap, and
        # increase miss chance by 2% when already capped or close.
        sign = 1 - 2 * int(self.player.miss_chance > 0.02)
        dps_deltas['1% hit'] = -0.5 * sign * self.calc_deriv(
            num_replicates, 'miss_chance', sign * 0.02, base_dps
        )

        # Crit is a simple increment
        dps_deltas['1% crit'] = 0.5 * self.calc_deriv(
            num_replicates, 'crit_chance', 0.02, base_dps
        )

        # Due to bearweaving, separate Agility weight calculation is needed
        dps_deltas['1 Agility'] = 1.0/40.0 * self.calc_deriv(
            num_replicates, 'agility', 40 * agi_mod, base_dps
        )

        # For haste we will use an increment of 4%. (Note that this is 4% in
        # one slot and not four individual 1% buffs.) We implement the
        # increment by reducing the player swing timer.
        base_haste_rating = calc_haste_rating(
            self.player.swing_timer, multiplier=self.haste_multiplier
        )
        swing_delta = self.player.swing_timer - calc_swing_timer(
            base_haste_rating + 63.08, multiplier=self.haste_multiplier
        )
        dps_deltas['1% haste'] = 0.25 * self.calc_deriv(
            num_replicates, 'swing_timer', -swing_delta, base_dps
        )

        # For armor pen, we use an increment of 50 Rating.
        dps_deltas['1 Armor Pen Rating'] = 1./50. * self.calc_deriv(
            num_replicates, 'armor_pen_rating', 50, base_dps
        )

        # For weapon damage, we use an increment of 12
        dps_deltas['1 Weapon Damage'] = 1./12. * self.calc_deriv(
            num_replicates, 'bonus_damage', 12, base_dps
        )

        # Calculate normalized stat weights
        stat_weights = {}

        for stat in dps_deltas:
            if stat != '1 AP':
                stat_weights[stat] = dps_deltas[stat] / dps_deltas['1 AP']

        return dps_deltas, stat_weights

    def calc_mana_weights(self, num_replicates, base_dps, dps_per_AP):
        """Calculate weights for mana stats in situations where the player goes
        oom before the end of the fight. It is assumed that the regular stat
        weights have already been calculated prior to calling this method.

        Arguments:
            num_replicates (int): Number of replicates to run.
            base_dps (float): Average base DPS before stat increments.
            dps_per_AP (float): DPS added by 1 AP. This is output by the
                calc_stat_weights() method, and is used to normalize the mana
                weights.

        Returns:
            dps_deltas (dict): Dictionary containing DPS increase from 1 Int,
                1 Spirit, and 1 mp5. Int and Spirit contributions are not
                boosted by ZG buff or Blessing of Kings, and should be adjusted
                accordingly.
            stat_weights (dict): Dictionary containing normalized stat weights
                for 1 Int, 1 Spirit, and 1 mp5 relative to 1 AP.
        """
        dps_deltas = {}

        # For mana weight, increment player mana pool by one shift's worth
        dps_deltas['1 mana'] = 1.0 / self.player.shift_cost * self.calc_deriv(
            num_replicates, 'mana_pool', self.player.shift_cost, base_dps
        )

        # For spirit weight, calculate how much spirit regens an additional
        # full shift's worth of mana over the course of Innervate.
        base_regen_delta = self.player.shift_cost / 10 / 5
        spirit_delta = base_regen_delta / self.player.regen_factor
        dps_deltas['1 Spirit'] = 1.0 / spirit_delta * self.calc_deriv(
            num_replicates, 'spirit', spirit_delta, base_dps
        )

        # Combine mana and regen contributions of Int
        mana_contribution = 15 * dps_deltas['1 mana']
        spirit_contribution = (
            self.player.spirit / (2 * self.player.intellect)
            * dps_deltas['1 Spirit']
        )
        dps_deltas['1 Int'] = mana_contribution + spirit_contribution

        # Same thing for mp5, except we integrate over the full fight length
        delta_mp5 = np.ceil(self.player.shift_cost / (self.fight_length / 5))
        dps_deltas['1 mp5'] = 1.0 / delta_mp5 * self.calc_deriv(
            num_replicates, 'mp5', delta_mp5, base_dps
        )

        # Calculate normalized stat weights
        stat_weights = {}

        for stat in ['1 mana', '1 Spirit', '1 Int', '1 mp5']:
            stat_weights[stat] = dps_deltas[stat] / dps_per_AP

        return dps_deltas, stat_weights
