"""
test_rapide.py
==============
Script autonome pour vérifier que l'installation fonctionne,
sans dépendre d'un fichier CSV externe.

Lancer avec : python test_rapide.py
"""

import pandas as pd
import numpy as np
from cameroun_map import CarteCameroun

# ── 1. On crée un faux CSV avec les 10 régions du Cameroun ──────────────────
np.random.seed(0)

donnees = pd.DataFrame({
    "region": [
        "Adamaoua", "Centre", "Est", "Extrême-Nord", "Littoral",
        "Nord", "Nord-Ouest", "Ouest", "Sud", "Sud-Ouest"
    ],
    "taux_pauvrete": np.random.uniform(20, 80, 10).round(1)
})

donnees.to_csv("donnees_regions.csv", index=False)
print("Fichier 'donnees_regions.csv' créé avec succès :\n")
print(donnees.to_string(index=False))
print()

# ── 2. On charge la carte (télécharge GADM la première fois) ────────────────
carte = CarteCameroun(niveau="regions")
carte.charger_geo()
carte.charger_metrique(donnees, col_nom="region", col_valeur="taux_pauvrete")
carte.diagnostiquer()
carte.valider()

# ── 3. On génère la carte statique ───────────────────────────────────────────
carte.visualiser(
    titre="Test — Taux de pauvreté par région",
    unite="%",
    methode="quantile",
    schema_couleur="OrRd",
    afficher_labels=True,
    sortie="test_carte.png"
)

print("\n✓ Test terminé. Vérifiez le fichier test_carte.png")
