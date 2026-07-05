"""
cameroun_map — visualisation cartographique du Cameroun à partir d'un CSV.

Usage minimal :
    from cameroun_map import CarteCameroun
    carte = CarteCameroun(niveau="regions")
    carte.charger_geo()
    carte.charger_metrique(df)
    carte.visualiser(sortie="carte.png")

Verbosité des logs (INFO par défaut) :
    import logging
    logging.getLogger("cameroun_map").setLevel(logging.WARNING)  # silencieux
    logging.getLogger("cameroun_map").setLevel(logging.DEBUG)    # très verbeux
"""

import logging as _logging

# Handler par défaut : affiche les messages INFO+ dans la console/notebook
# sans polluer le logger racine. Un seul handler évite les doublons si le
# module est rechargé (ex : ipython autoreload).
_log = _logging.getLogger("cameroun_map")
if not _log.handlers:
    _h = _logging.StreamHandler()
    _h.setFormatter(_logging.Formatter("%(message)s"))
    _log.addHandler(_h)
    _log.setLevel(_logging.INFO)
    _log.propagate = False  # ne remonte pas au logger racine

from .cameroun_viz import (
    CarteCameroun,
    CarteCamerounError,
    Style,
    charger_gadm,
    charger_osm_quartiers,
    joindre_metrique,
    classifier_valeurs,
    GADM_LEVELS,
    CRS_LOCAL,
    CRS_GLOBAL,
    REGIONS_OFFICIELLES,
    SCHEMAS_COULEUR,
    METHODES_CLASSIFICATION,
    SYMBOLES_PREDEFINIS,
)
from .adapter_dataset import (
    diagnostiquer_dataset,
    adapter_dataset,
    normaliser_nom,
    normaliser_nom_avec_score,
    large_vers_long,
    separer_par_niveau,
    normaliser_par_population,
)
from .export_powerbi import exporter_pour_powerbi

__all__ = [
    "CarteCameroun",
    "CarteCamerounError",
    "Style",
    "SYMBOLES_PREDEFINIS",
    "charger_gadm",
    "charger_osm_quartiers",
    "joindre_metrique",
    "classifier_valeurs",
    "GADM_LEVELS",
    "CRS_LOCAL",
    "CRS_GLOBAL",
    "REGIONS_OFFICIELLES",
    "SCHEMAS_COULEUR",
    "METHODES_CLASSIFICATION",
    "diagnostiquer_dataset",
    "adapter_dataset",
    "normaliser_nom",
    "normaliser_nom_avec_score",
    "large_vers_long",
    "separer_par_niveau",
    "normaliser_par_population",
    "exporter_pour_powerbi",
]

__version__ = "0.1.0"
