"""Code for simulating the classic WoW feral cat DPS rotation."""

import numpy as np
import copy
import collections
import urllib
import multiprocessing
import psutil


def calc_white_damage(
    low_end, high_end, miss_chance, crit_chance,
    crit_multiplier=2.0
):
    """Execute single roll table for a melee white attack.

    Arguments:
        low_end (float): Low end base damage of the swing.
        high_end (float): High end base damage of the swing.
        miss_chance (float): Probability that the swing is avoided.
        crit_chance (float): Probability of a critical strike.
        crit_multiplier (float): Damage multiplier on crits.
            Defaults to 2.0.

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
        return crit_multiplier * base_dmg, False, True
    return base_dmg, False, False


def calc_yellow_damage(
    low_end, high_end, miss_chance, crit_chance,
    crit_multiplier=2.0
):
    """Execute 2-roll table for a melee spell.

    Arguments:
        low_end (float): Low end base damage of the ability.
        high_end (float): High end base damage of the ability.
        miss_chance (float): Probability that the ability is avoided.
        crit_chance (float): Probability of a critical strike.
        crit_multiplier (float): Damage multiplier on crits.
            Defaults to 2.0.

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
        return crit_multiplier * base_dmg, False, True
    return base_dmg, False, False

def calc_spell_damage(
    low_end, high_end, miss_chance, crit_chance, crit_multiplier=1.5
):
    """Execute 2-roll table for a spell and adjust for resistances.

    Arguments:
        low_end (float): Low end base damage of the ability.
        high_end (float): High end base damage of the ability.
        miss_chance (float): Probability that the ability is avoided.
        crit_chance (float): Probability of a critical strike.
        crit_multiplier (float): Damage multiplier on crits.
            Defaults to 1.5.

    Returns:
        damage_done (float): Damage done by the ability.
        miss (bool): True if the attack was avoided.
        crit (bool): True if the attack was a critical strike.
    """
    base_dmg, miss, crit = calc_yellow_damage(low_end, high_end, 
        miss_chance, crit_chance, crit_multiplier)
    # Adjust for resistances, hard coded for pure level based resist
    if not miss:
        resist_roll = np.random.rand()
        if resist_roll < 0.55:
            base_dmg *= 1.0
        elif resist_roll < 0.85:
            base_dmg *= 0.9
        else:
            base_dmg *= 0.8
    return base_dmg, miss, crit


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
    return base_timer / (multiplier * (1 + haste_rating / 2521))


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
    return 2521 * (base_timer / (swing_timer * multiplier) - 1)


def calc_hasted_gcd(haste_rating, multiplier=1.0):
    """Calculate GCD for spell casts given a total haste rating stat.

    Arguments:
        haste_rating (int): Player haste rating stat.
        multiplier (float): Overall spell haste multiplier from multiplicative
            haste buffs such as Bloodlust. Defaults to 1.

    Returns:
        spell_gcd (float): Hasted GCD in seconds.
    """
    return max(1.5 / (multiplier * (1 + haste_rating / 3279)), 1.0)


def gen_import_link(
    stat_weights, EP_name='Simmed Weights', multiplier=1.166, epic_gems=False
):
    """Generate 80upgrades stat weight import link from calculated weights.

    Socket value is determined by the largest non-hit weight, as hit will
    often be too close to cap to socket. Note that ArP gems may still run
    into cap issues.

    Arguments:
        stat_weights (dict): Dictionary of weights generated by a Simulation
            object. Required keys are: "1% hit", "1% crit", "1% haste",
            "1% expertise", "1 Armor Pen", "1 Agility" and "1 Weapon Damage".
        EP_name (str): Name for the EP set for auto-populating the 70upgrades
            import interface. Defaults to "Simmed Weights".
        multiplier (float): Scaling factor for raw primary stats. Defaults to
            1.166 assuming Blessing of Kings, 3/3 Survival of the Fittest and
            0/2 Improved Mark of the Wild.
        epic_gems (bool): Whether Epic quality gems (20 Stats) should be
            assumed for socket weight calculations. Defaults to False (Rare
            quality +16 gems).

    Returns:
        import_link (str): Full URL for stat weight import into 80upgrades.
    """
    link = 'https://eightyupgrades.com/ep/import?name='

    # EP Name
    link += urllib.parse.quote(EP_name)

    # Attack Power and Strength
    link += '&31=1&33=1.2&4=%.3f' % (2 * multiplier)

    # Agility
    # Due to bear weaving, agi is no longer directly derived from
    # AP and crit.
    agi_weight = stat_weights['1 Agility']
    link += '&0=%.2f' % agi_weight

    # Hit Rating and Expertise Rating
    hit_weight = stat_weights['1% hit'] / 32.79
    link += '&35=%.2f' % (hit_weight)

    # Expertise Rating
    expertise_weight = stat_weights['1% expertise'] / 32.79
    link += '&46=%.2f' % (expertise_weight)

    # Critical Strike Rating
    crit_weight = stat_weights['1% crit'] / 45.91
    link += '&41=%.2f' % (crit_weight)

    # Haste Rating
    haste_weight = stat_weights['1% haste'] / 25.21
    link += '&43=%.2f' % (haste_weight)

    # Armor Penetration
    link += '&87=%.2f' % stat_weights['1 Armor Pen Rating']

    # Weapon Damage
    link += '&51=%.2f' % stat_weights['1 Weapon Damage']

    # Gems
    gem_size = 20 if epic_gems else 16
    gem_weight = (
        max(agi_weight, crit_weight, haste_weight,
        stat_weights["1 Armor Pen Rating"]) * gem_size
    )
    link += '&74=%.2f&75=%.2f&76=%.2f' % (gem_weight, gem_weight, gem_weight)

    return link
