"""
cameroun_map — visualisation cartographique du Cameroun à partir d'un CSV.

Usage minimal :
    from cameroun_map import CarteCameroun
    carte = CarteCameroun(niveau="regions")
    carte.charger_geo()
    carte.charger_metrique(df)
    carte.visualiser(sortie="carte.png")
"""

from .cameroun_viz import (
    CarteCameroun,
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
