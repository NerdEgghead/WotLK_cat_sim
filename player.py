"""Code for simulating the classic WoW feral cat DPS rotation."""

import numpy as np
import collections
import sim_utils


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
    def spell_hit_chance(self):
        return self._spell_hit_chance
    
    @spell_hit_chance.setter
    def spell_hit_chance(self, value):
        self._spell_hit_chance = value
        self.calc_spell_miss_chance()

    @property
    def expertise_rating(self):
        return self._expertise_rating

    @expertise_rating.setter
    def expertise_rating(self, value):
        self._expertise_rating = value
        self.calc_miss_chance()

    roar_durations = {1: 14.0, 2: 19.0, 3: 24.0, 4: 29.0, 5: 34.0}

    def __init__(
            self, attack_power, ap_mod, agility, hit_chance, spell_hit_chance, 
            expertise_rating, crit_chance, spell_crit_chance, armor_pen_rating, 
            swing_timer, mana, intellect, spirit, mp5, jow=False, rune=True, t6_2p=False,
            t6_4p=False, t7_2p=False, wolfshead=True, mangle_glyph=False, meta=False,
            bonus_damage=0, shred_bonus=0, rip_bonus=0, debuff_ap=0, multiplier=1.1, 
            spell_damage_multiplier=1.0, omen=True, primal_gore=True, feral_aggression=0,
            predatory_instincts=3, savage_fury=2, furor=3,
            natural_shapeshifter=3, intensity=0, potp=2, improved_mangle=0,
            ilotp=2, rip_glyph=True, shred_glyph=True, roar_glyph=False,
            berserk_glyph=False, weapon_speed=3.0, gotw_targets=25,
            t8_2p=False, t8_4p=False, t9_2p=False, t9_4p=False, 
            t10_2p=False, t10_4p=False, proc_trinkets=[], log=False
    ):
        """Initialize player with key damage parameters.

        Arguments:
            attack_power (int): Fully raid buffed attack power in Cat Form.
            ap_mod (float): Total multiplier for Attack Power in Cat Form.
            agility (int): Fully raid buffed Agility attribute.
            hit_chance (float): Chance to hit as a fraction.
            spell_hit_chance (float): Chance for spells (faerie fire feral) 
                to hit as a fraction.
            expertise_rating (int): Player's Expertise Rating stat.
            crit_chance (float): Fully raid buffed crit chance as a fraction.
            spell_crit_chance (float): Fully raid buffed spell crit chance 
                as a fraction.
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
            rune (bool): Whether Dark/Demonic Runes are used. Defaults True.
            t6_2p (bool): Whether the 2-piece T6 set bonus is used. Defaults
                False.
            t6_4p (bool): Whether the 4-piece T6 set bonus is used. Defaults
                False.
            t7_2p (bool): Whether the 2-piece T7 set bonus is used. Defaults
                False.
            wolfshead (bool): Whether Wolfshead is worn. Defaults to True.
            mangle_glyph (bool): Whether Glyph of Mangle is used. Defaults False.
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
            predatory_instincts (int): Points taken in Predatory Instincts
                talent. Defaults to 3.
            savage_fury (int): Points taken in Savage Fury talent. Defaults
                to 0.
            furor (int): Points taken in Furor talent. Default to 3.
            natural_shapeshifter (int): Points taken in Natural Shapeshifter
                talent. Defaults to 3.
            intensity (int): Points taken in Intensity talent. Defaults to 0.
            potp (int): Points taken in Protector of the Pack talent. Defaults
                to 2.
            improved_mangle (int): Points taken in Improved Mangle talent.
                Defaults to 0.
            ilotp (int): Points taken in Improved Leader of the Pack talent.
                Defaults to 2.
            rip_glyph (bool): Whether Glyph of Rip is used. Defaults True.
            shred_glyph (bool): Whether Glyph of Shred is used. Defaults True.
            roar_glyph (bool): Whether Glyph of Savage Roar is used. Defaults
                False.
            berserk_glyph (bool): Whether Glyph of Berserk is used. Defaults
                False.
            weapon_speed (float): Equipped weapon speed, used for calculating
                swing timer resets from instant spell casts. Defaults to 3.0.
            gotw_targets (int): Number of targets that will be buffed if the
                player casts Gift of the Wild during combat. Used for
                calculating Omen of Clarity proc chance. Defaults to 25.
            t8_2p (bool): Whether the 2-piece T8 set bonus is used. Defaults
                False.
            t8_4p (bool): Whether the 4-piece T8 set bonus is used. Defaults
                False.
            t9_2p (bool): Whether the 2-piece T9 set bonus is used. Defaults
                False.
            t9_4p (bool): Whether the 4-piece T9 set bonus is used. Defaults
                False.
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
        self.roar_fac = 0.3 + 0.03 * roar_glyph
        self.berserk_glyph = berserk_glyph
        self.mangle_glyph = mangle_glyph
        self.shred_glyph = shred_glyph
        self.rip_duration = 12 + 4 * rip_glyph + 4 * t7_2p
        self.rake_duration = 9 + 3 * t9_2p
        self.lacerate_multi = (1 + 0.05 * t7_2p) * (1 + 0.2 * t10_2p)
        self.lacerate_dot_multi = (1 + 0.05 * t9_2p) * (1 + 0.2 * t10_2p)
        self.bite_crit_bonus = 0.25 + 0.05 * t9_4p
        self.rip_crit_bonus = 0.05 * t9_4p

        # Set internal hit and expertise values, and derive total miss chance.
        self._hit_chance = hit_chance
        self.spell_hit_chance = spell_hit_chance
        self.expertise_rating = expertise_rating

        self.crit_chance = crit_chance - 0.048
        # Assume no spell crit suppression for now.
        self.spell_crit_chance = spell_crit_chance
        self.armor_pen_rating = armor_pen_rating
        self.swing_timer = swing_timer
        self.mana_pool = mana
        self.intellect = intellect
        self.spirit = spirit
        self.mp5 = mp5
        self.jow = jow
        self.rune = rune
        self.bonus_damage = bonus_damage
        self.shred_bonus = shred_bonus
        self.rip_bonus = rip_bonus
        self._mangle_cost = 40 - 5 * t6_2p - 2 * improved_mangle
        self.t6_bonus = t6_4p
        self.t8_2p_bonus = t8_2p
        self.t8_4p_bonus = t8_4p
        self.t10_2p_bonus = t10_2p
        self.t10_4p_bonus = t10_4p
        self._rip_cost = 30 - 10 * self.t10_2p_bonus
        self.wolfshead = wolfshead
        self.meta = meta
        self.damage_multiplier = multiplier
        self.spell_damage_multiplier = spell_damage_multiplier
        self.omen = omen
        self.primal_gore = primal_gore
        self.feral_aggression = feral_aggression
        self.predatory_instincts = predatory_instincts
        self.savage_fury = savage_fury
        self.furor = furor
        self.natural_shapeshifter = natural_shapeshifter
        self.intensity = intensity
        self.ilotp = ilotp
        self.weapon_speed = weapon_speed
        self.omen_rates = {
            'white': 3.5/60,
            'yellow': 0.0,
            'bear': 3.5/60*2.5,
            'gotw': 1 - (1 - 0.0875)**gotw_targets
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
            6.5, (10 + np.floor(self._expertise_rating / 8.1974973675)) * 0.25
        )
        self.miss_chance = 0.01 * (
            (8. - miss_reduction) + (6.5 - dodge_reduction)
        )
        self.dodge_chance = 0.01 * (6.5 - dodge_reduction)

    def calc_spell_miss_chance(self):
        """Update spell miss chance when a change to the player's spell
        hit percent occurs."""
        spell_miss_reduction = min(self._spell_hit_chance * 100, 17.0)
        self.spell_miss_chance = 0.01 * (17.0 - spell_miss_reduction)

    def calc_crit_multiplier(self):
        crit_multiplier = 2.0 * (1.0 + self.meta * 0.03)
        if self.cat_form:
            crit_multiplier *= (1.0 + round(self.predatory_instincts / 30, 2))
        return crit_multiplier
    
    def calc_spell_crit_multiplier(self):
        spell_crit_multiplier = 1.5 * (1.0 + self.meta * 0.03)
        return spell_crit_multiplier

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
        self.regen_factor = 0.016725 / 5 * np.sqrt(self.intellect)
        base_regen = self.spirit * self.regen_factor
        bonus_regen = self.mp5 / 5

        # In TBC, the Intensity talent allows a portion of the base regen to
        # apply while within the five second rule
        self.regen_rates = {
            'base': base_regen + bonus_regen,
            'five_second_rule': 0.5/3*self.intensity*base_regen + bonus_regen,
        }
        self.shift_cost = 1224 * 0.4 * (1 - 0.1 * self.natural_shapeshifter)

    def calc_damage_params(
            self, gift_of_arthas, boss_armor, sunder, faerie_fire,
            blood_frenzy, curse_of_elements, shattering_throw, tigers_fury=False
    ):
        """Calculate high and low end damage of all abilities as a function of
        specified boss debuffs."""
        bonus_damage = (
            (self.attack_power + self.debuff_ap) / 14 + self.bonus_damage
            + 80 * tigers_fury
        )

        # Legacy compatibility with older Sunder code in case it is needed
        if isinstance(sunder, bool):
            sunder *= 5

        debuffed_armor = (
            boss_armor * (1 - 0.04 * sunder) * (1 - 0.05 * faerie_fire)
            * (1 - 0.2 * shattering_throw)
        )
        armor_constant = 467.5 * 80 - 22167.5
        arp_cap = (debuffed_armor + armor_constant) / 3.
        armor_pen = (
            min(1399, self.armor_pen_rating) / 13.99 / 100 * min(arp_cap, debuffed_armor)
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
            self.white_low * 2.25 + (666 + self.shred_bonus) * self.multiplier
        )
        self.shred_high = 1.2 * (
            self.white_high * 2.25 + (666 + self.shred_bonus) * self.multiplier
        )
        self.bite_multiplier = (
            self.multiplier * (1 + 0.03 * self.feral_aggression)
            * (1 + 0.15 * self.t6_bonus)
        )

        # Tooltip low range base values for Bite are 935 and 766, but that's
        # incorrect according to the DB.
        ap, bm = self.attack_power, self.bite_multiplier
        self.bite_low = {
            i: (290*i + 120 + 0.07 * i * ap) * bm for i in range(1, 6)
        }
        self.bite_high = {
            i: (290*i + 260 + 0.07 * i * ap) * bm for i in range(1, 6)
        }
        sf_fac = 1 + 0.1 * self.savage_fury
        mangle_fac = sf_fac * (1 + 0.1 * self.mangle_glyph)
        self.mangle_low = mangle_fac * (
            self.white_low * 2 + 566 * self.multiplier
        )
        self.mangle_high = mangle_fac * (
            self.white_high * 2 + 566 * self.multiplier
        )
        rake_multi = sf_fac * damage_multiplier
        self.rake_hit = rake_multi * (176 + 0.01 * self.attack_power)
        self.rake_tick = rake_multi * (358 + 0.06 * self.attack_power)
        rip_multiplier = damage_multiplier * (1 + 0.15 * self.t6_bonus)
        self.rip_tick = {
            i: (36 + 93*i + 0.01*i*ap + self.rip_bonus*i) * rip_multiplier
            for i in range(1,6)
        }

        # Bearweave damage calculations
        bear_ap = self.bear_ap_mod * (
            self.attack_power / self.ap_mod - self.agility + 80
        )
        bear_bonus_damage = (
            (bear_ap + self.debuff_ap) / 14 * 2.5 + self.bonus_damage
        )
        bear_multi = self.multiplier * 1.04 # Master Shapeshifter
        self.white_bear_low = (109.0 + bear_bonus_damage) * bear_multi
        self.white_bear_high = (165.0 + bear_bonus_damage) * bear_multi
        maul_multi = sf_fac * 1.2
        self.maul_low = (self.white_bear_low + 578 * bear_multi) * maul_multi
        self.maul_high = (self.white_bear_high + 578 * bear_multi) * maul_multi
        self.mangle_bear_low = mangle_fac * (
            self.white_bear_low * 1.15 + 299 * bear_multi
        )
        self.mangle_bear_high = mangle_fac * (
            self.white_bear_high * 1.15 + 299 * bear_multi
        )
        lacerate_multi = bear_multi * self.lacerate_multi
        self.lacerate_hit = (88 + 0.01 * bear_ap) * lacerate_multi
        self.lacerate_tick = (64+0.01*bear_ap) * bear_multi / armor_multiplier

        # Bear Faerie Fire damage calculations
        self.faerie_fire_hit = (0.15 * bear_ap + 1.) * (1 + 0.13 * curse_of_elements) \
                                    * self.spell_damage_multiplier

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

    def update_spell_gcd(self, haste_rating, multiplier=None):
        """Update GCD length for Gift of the Wild when player Spell Haste is
        modified.

        Arguments:
            haste_rating (int): Updated Haste Rating stat.
            multiplier (float): Overall Spell Haste multiplier from
                multiplicative Haste buffs such as Bloodlust. If provided,
                the new multiplier will be stored for future use. If omitted,
                the previously stored multiplier will be used instead.
        """
        if multiplier is not None:
            self.spell_haste_multiplier = multiplier

        self.spell_gcd = sim_utils.calc_hasted_gcd(
            haste_rating, multiplier=self.spell_haste_multiplier
        )

    def reset(self):
        """Reset fight-specific parameters to their starting values at the
        beginning of an encounter."""
        self.gcd = 0.0
        self.omen_proc = False
        self.ilotp_icd = 0.0
        self.tf_cd = 0.0
        self.energy = 100
        self.combo_points = 0
        self.mana = self.mana_pool
        self.rage = 0
        self.rune_cd = 0.0
        self.five_second_rule = False
        self.cat_form = True
        self.ready_to_shift = False
        self.ready_to_gift = False
        self.berserk = False
        self.berserk_cd = 0.0
        self.enrage = False
        self.enrage_cd = 0.0
        self.mangle_cd = 0.0
        self.faerie_fire_cd = 0.0
        self.savage_roar = False
        self.dagger_equipped = False
        self.set_ability_costs()

        # Create dictionary to hold breakdown of total casts and damage
        self.dmg_breakdown = collections.OrderedDict()

        for cast_type in [
            'Melee', 'Mangle (Cat)', 'Rake', 'Shred', 'Savage Roar', 'Rip',
            'Ferocious Bite', 'Faerie Fire (Cat)', 'Shift (Bear)', 'Maul',
            'Mangle (Bear)', 'Lacerate', 'Shift (Cat)', 'Gift of the Wild',
            'Faerie Fire (Bear)'
        ]:
            self.dmg_breakdown[cast_type] = {'casts': 0, 'damage': 0.0}

    def set_ability_costs(self):
        """Store Energy costs for all specials in the rotation based on whether
        or not Berserk is active."""
        self.shred_cost = 42. / (1 + self.berserk)
        self.rake_cost = 35. / (1 + self.berserk)
        self.mangle_cost = self._mangle_cost / (1 + self.berserk)
        self.bite_cost = 35. / (1 + self.berserk)
        self.rip_cost = self._rip_cost / (1 + self.berserk)
        self.roar_cost = 25. / (1 + self.berserk)

    def check_omen_proc(self, yellow=False):
        """Check for Omen of Clarity proc on a successful swing.

        Arguments:
            yellow (bool): Check proc for a yellow ability rather than a melee
                swing. Defaults False.
        """
        if (not self.omen) or yellow:
            return

        if self.cat_form:
            proc_rate = self.omen_rates['white']
        else:
            proc_rate = self.omen_rates['bear']

        proc_roll = np.random.rand()

        if proc_roll < proc_rate:
            self.omen_proc = True

    def check_jow_proc(self):
        """Check for a Judgment Wisdom on a successful melee attack."""
        if not self.jow:
            return

        proc_roll = np.random.rand()

        if proc_roll < 0.25:
            self.mana = min(self.mana + 70, self.mana_pool)

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

        if crit and (self.ilotp_icd < 1e-9):
            self.mana = min(
                self.mana + 0.04 * self.ilotp * self.mana_pool, self.mana_pool
            )
            self.ilotp_icd = 6.0

        # Now check for all trinket procs that may occur. Only trinkets that
        # can trigger on all possible abilities will be checked here. The
        # handful of proc effects that trigger only on Mangle must be
        # separately checked within the mangle() function.
        for trinket in self.proc_trinkets:
            if not trinket.special_proc_conditions:
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
        self.rune_cd = 15. * 60.
        return True

    def swing(self):
        """Execute a melee swing.

        Returns:
            damage_done (float): Damage done by the swing.
        """
        low = self.white_low if self.cat_form else self.white_bear_low
        high = self.white_high if self.cat_form else self.white_bear_high
        damage_done, miss, crit = sim_utils.calc_white_damage(
            low, high, self.miss_chance,
            self.crit_chance - 0.04 * (not self.cat_form),
            crit_multiplier=self.calc_crit_multiplier()
        )

        # Apply King of the Jungle for bear form swings
        if self.enrage:
            damage_done *= 1.15

        # Apply Savage Roar for cat form swings
        if self.cat_form and self.savage_roar:
            roar_damage = self.roar_fac * damage_done
        else:
            roar_damage = 0.0

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
                    15./4./453.3 * proxy_damage + 2.5/2*3.5 * (1 + crit)
                    + 5 * crit
                )
                rage_gen = min(rage_gen, proxy_damage * 15. / 453.3)
                self.rage = min(self.rage + rage_gen, 100)

        # Log the swing
        self.dmg_breakdown['Melee']['casts'] += 1
        self.dmg_breakdown['Melee']['damage'] += damage_done
        self.dmg_breakdown['Savage Roar']['damage'] += roar_damage

        if self.log:
            self.gen_log('melee', damage_done + roar_damage, miss, crit, False)

        return damage_done + roar_damage

    def execute_bear_special(
        self, ability_name, min_dmg, max_dmg, rage_cost, yellow=True,
        mangle_mod=False
    ):
        """Execute a special ability cast in Dire Bear form.

        Arguments:
            ability_name (str): Name of the ability for use in logging.
            min_dmg (float): Low end damage of the ability.
            max_dmg (float): High end damage of the ability.
            rage_cost (int): Rage cost of the ability.
            yellow (bool): Whether the ability should be treated as "yellow
                damage" for the purposes of proc calculations. Defaults True.
            mangle_mod (bool): Whether to apply the Mangle damage modifier to
                the ability. Default False.

        Returns:
            damage_done (float): Damage done by the ability.
            success (bool): Whether the ability successfully landed.
        """
        # Perform Monte Carlo
        damage_done, miss, crit = sim_utils.calc_yellow_damage(
            min_dmg, max_dmg, self.miss_chance, self.crit_chance - 0.04,
            crit_multiplier=self.calc_crit_multiplier()
        )

        if mangle_mod:
            damage_done *= 1.3

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
            self.check_procs(crit=crit, yellow=True)

        # Log the cast
        self.dmg_breakdown[ability_name]['casts'] += 1
        self.dmg_breakdown[ability_name]['damage'] += damage_done

        if self.log:
            self.gen_log(ability_name, damage_done, miss, crit, clearcast)

        return damage_done, not miss

    def maul(self, mangle_debuff):
        """Execute a Maul when in Dire Bear Form.

        Arguments:
            mangle_debuff (bool): Whether the Mangle debuff is applied when
                Maul is cast.

        Returns:
            damage_done (float): Damage done by the Maul cast.
        """
        damage_done, success = self.execute_bear_special(
            'Maul', self.maul_low, self.maul_high, 10, yellow=False,
            mangle_mod=mangle_debuff
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
        damage_done, miss, crit = sim_utils.calc_yellow_damage(
            min_dmg, max_dmg, self.miss_chance, self.crit_chance,
            crit_multiplier=self.calc_crit_multiplier()
        )

        if mangle_mod:
            damage_done *= 1.3

        # Apply Savage Roar
        roar_damage = self.roar_fac * damage_done if self.savage_roar else 0.0

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
        self.dmg_breakdown['Savage Roar']['damage'] += roar_damage

        if self.log:
            self.gen_log(
                ability_name, damage_done + roar_damage, miss, crit, clearcast
            )

        return damage_done + roar_damage, not miss

    def shred(self, mangle_debuff):
        """Execute a Shred.

        Arguments:
            mangle_debuff (bool): Whether the Mangle debuff is applied when
                Shred is cast.

        Returns:
            damage_done (float): Damage done by the Shred cast.
            success (bool): Whether the Shred landed successfully.
        """
        damage_done, success = self.execute_builder(
            'Shred', self.shred_low, self.shred_high, self.shred_cost,
            mangle_mod=mangle_debuff
        )

        # Since a handful of proc effects trigger only on Shred, we separately
        # check for those procs here if the Shred landed successfully.
        if success:
            for trinket in self.proc_trinkets:
                if trinket.shred_only:
                    trinket.check_for_proc(False, True)

        return damage_done, success

    def rake(self, mangle_debuff):
        """Execute a Rake.

        Arguments:
            mangle_debuff (bool): Whether the Mangle debuff is applied when
                Rake is cast.

        Returns:
            damage_done (float): Damage done by the Rake cast.
            success (bool): Whether the Rake landed successfully.
        """
        damage_done, success = self.execute_builder(
            'Rake', self.rake_hit, self.rake_hit, self.rake_cost,
            mangle_mod=mangle_debuff
        )
        return damage_done, success

    def lacerate(self, mangle_debuff):
        """Execute a Lacerate.

        Arguments:
            mangle_debuff (bool): Whether the Mangle debuff is applied when
                Lacerate is cast.

        Returns:
            damage_done (float): Damage done just by the Lacerate cast itself.
            success (bool): Whether the Lacerate debuff was successfully
                applied or refreshed.
        """
        return self.execute_bear_special(
            'Lacerate', self.lacerate_hit, self.lacerate_hit, 13,
            mangle_mod=mangle_debuff
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
                if trinket.cat_mangle_only and self.cat_form:
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
            min(self.energy, 30) * (9.4 + self.attack_power / 410.)
            * self.bite_multiplier
        )

        # Perform Monte Carlo
        damage_done, miss, crit = sim_utils.calc_yellow_damage(
            self.bite_low[self.combo_points] + bonus_damage,
            self.bite_high[self.combo_points] + bonus_damage, self.miss_chance,
            self.crit_chance + self.bite_crit_bonus,
            crit_multiplier=self.calc_crit_multiplier()
        )

        # Apply Savage Roar
        roar_damage = self.roar_fac * damage_done if self.savage_roar else 0.0

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
        self.dmg_breakdown['Savage Roar']['damage'] += roar_damage

        if self.log:
            self.gen_log(
                'Ferocious Bite', damage_done + roar_damage, miss, crit,
                clearcast
            )

        return damage_done + roar_damage

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

        if self.log:
            self.gen_log('Rip', 'applied', miss, False, clearcast)

        return damage_per_tick, not miss

    def roar(self, time):
        """Cast Savage Roar as a finishing move.

        Arguments:
            time (float): Time at which the Roar cast is executed, in seconds.

        Returns:
            roar_end (float): Time at which the Savage Roar buff will expire.
        """
        # Set GCD
        self.gcd = 1.0

        # Update Energy - SR ignores Clearcasting
        self.energy -= self.roar_cost

        # Apply buff
        self.savage_roar = True
        roar_end = time + self.roar_durations[self.combo_points] + 8 * self.t8_4p_bonus
        self.combo_points = 0

        # Log the cast
        self.dmg_breakdown['Savage Roar']['casts'] += 1

        if self.log:
            self.gen_log('Savage Roar', 'applied', False, False, False)

        return roar_end

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

        if self.log:
            if powershift:
                cast_name = 'Powers' + cast_name[1:]

            self.combat_log = [
                cast_name, log_str, '%.1f' % self.energy,
                '%d' % self.combo_points, '%d' % self.mana, '%d' % self.rage
            ]

    def flowershift(self, time):
        """Cast Gift of the Wild in order to fish for a Clearcasting proc.

        Arguments:
            time (float): Time at which the cast is executed, in seconds. Used
                for determining the five second rule.
        """
        # Execute the cast and perform related bookkeeping
        self.cat_form = False
        self.gcd = self.spell_gcd
        self.dmg_breakdown['Gift of the Wild']['casts'] += 1
        self.mana -= 1119 # Glyph of the Wild assumed
        self.five_second_rule = True
        self.last_shift = time
        self.ready_to_gift = False

        # Check for Clearcasting proc
        if self.omen and (np.random.rand() < self.omen_rates['gotw']):
            self.omen_proc = True

        # Log the cast
        if self.log:
            self.gen_log('Gift of the Wild', '', False, False, False)

    def faerie_fire(self):
        """Cast Faerie Fire (Feral) for a guaranteed Clearcasting proc.

        Returns:
            damage_done (float): Damage done by the Faerie Fire cast. Always 0
                at present since only Cat Form casts are being modeled, but can
                be modified to return non-zero damage for Dire Bear Form casts.
        """
        self.gcd = 1.0
        self.omen_proc = True
        self.faerie_fire_cd = 6.0

        if self.cat_form:
            self.dmg_breakdown['Faerie Fire (Cat)']['casts'] += 1
            if self.log:
                self.gen_log('Faerie Fire (Cat)', '', False, False, False)
            return 0.0

        # Perform spell damage calculation for Bear Faerie Fire
        damage_done, miss, crit = sim_utils.calc_spell_damage(
            self.faerie_fire_hit, self.faerie_fire_hit, self.spell_miss_chance, 
            self.spell_crit_chance, crit_multiplier=self.calc_spell_crit_multiplier()
        )
        if self.enrage:
            damage_done *= 1.15
        self.dmg_breakdown['Faerie Fire (Bear)']['casts'] += 1    
        self.dmg_breakdown['Faerie Fire (Bear)']['damage'] += damage_done  
        if self.log:
            self.gen_log('Faerie Fire (Bear)', damage_done, miss, crit, False)
        return damage_done
