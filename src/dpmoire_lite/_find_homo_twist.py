from __future__ import annotations

import numpy as np
from ase import Atoms


def search_twist(N_min: int = 2, N_max: int = 10):
    mat_list = []
    angle_list = []
    for i in range(N_min, N_max + 1):
        mat_1 = np.array([[i, i + 1, 0], [-(i + 1), 2 * i + 1, 0], [0, 0, 1]]).T
        mat_2 = np.array([[i + 1, i, 0], [-i, 2 * i + 1, 0], [0, 0, 1]]).T
        mat_list.append((mat_1, mat_2))
        angle_list.append("{0:.5f}".format(np.arccos((3 * i**2 + 3 * i + 0.5) / (3 * i**2 + 3 * i + 1)) / np.pi * 180))
    return angle_list, mat_list


def adjust_atoms_d(top_atoms: Atoms, bot_atoms: Atoms, d: float):
    top_pos = top_atoms.get_positions()
    bot_pos = bot_atoms.get_positions()
    top_pos[:, 2] += d / 2 - float(top_pos[:, 2].min())
    bot_pos[:, 2] += -d / 2 - float(bot_pos[:, 2].max())
    top_atoms.set_positions(top_pos)
    bot_atoms.set_positions(bot_pos)
    return top_atoms, bot_atoms
