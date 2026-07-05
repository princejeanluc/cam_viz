"""
Visualisation cartographique du Cameroun — solution modulaire
Supporte : régions, départements, arrondissements, quartiers (OSM)
"""

import geopandas as gpd
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import BoundaryNorm, ListedColormap, LinearSegmentedColormap
from matplotlib import cm
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
import folium
from folium.plugins import FloatImage
import plotly.express as px
import requests, zipfile, io, os, re
from pathlib import Path
from dataclasses import dataclass, field
import logging
import warnings
warnings.filterwarnings("ignore")


@dataclass
class Style:
    """
    Contrôle l'apparence visuelle d'une carte.
    Passez un objet Style à CarteCameroun(style=...) pour personnaliser le rendu.

    Exemple publication (fond blanc, palette sobre) :
        Style(fond_blanc=True, palette="froid")
    Exemple impact (rouge chaleur, haute résolution) :
        Style(palette="chaleur", dpi=300)
    """
    # Palette choroplèthe — liste de hex OU clé de SCHEMAS_COULEUR, ou None
    palette: list = None

    # Fonds
    fond_figure: str  = "white"     # marge autour de la carte (blanc = propre)
    fond_carte:  str  = "#EBF5FB"   # zone eau/fond (bleu très pâle)
    fond_blanc:  bool = False        # raccourci mode publication strict

    # Contours
    contours_zones_couleur:    str   = "white"
    contours_zones_epaisseur:  float = 0.6
    contours_pays_couleur:     str   = "#333333"
    contours_pays_epaisseur:   float = 0.3

    # Typographie — hiérarchie fixe pour cohérence entre cartes
    police:           str   = None   # famille ("Arial") ou None → DejaVu Sans
    taille_titre:     int   = 15
    taille_sous_titre: int  = 11
    taille_labels:    float = 7.0
    taille_legende:   int   = 8
    taille_source:    int   = 7
    couleur_titre:    str   = "#1A1A1A"

    # Zones sans données
    manquant_couleur: str = "#D0D0D0"
    manquant_hatch:   str = "///"

    # Décoration
    bordure_carte: bool = True  # cadre fin #CCCCCC autour de la zone cartographique

    # Résolution export
    dpi: int = 180

    def __post_init__(self):
        if self.fond_blanc:
            self.fond_figure = "white"
            self.fond_carte  = "#F7F9FC"


# ─────────────────────────────────────────────────────────────────────────────
# EXCEPTIONS & LOGGING
# ─────────────────────────────────────────────────────────────────────────────

class CarteCamerounError(RuntimeError):
    """Exception levée par la librairie cameroun_map avec un message actionnable."""


logger = logging.getLogger("cameroun_map")

try:
    from thefuzz import process as fuzzy_process   # pip install thefuzz
    _FUZZY_DISPONIBLE = True
except ImportError:
    _FUZZY_DISPONIBLE = False

from .adapter_dataset import (
    diagnostiquer_dataset,
    adapter_dataset,
    detecter_colonne_zone,
    detecter_colonnes_valeur,
    normaliser_nom,
    normaliser_nom_avec_score,
)

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONSTANTES & CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# Niveaux administratifs disponibles via GADM
GADM_LEVELS = {
    "pays":           0,   # contour national
    "regions":        1,   # 10 régions
    "departements":   2,   # 58 départements
    "arrondissements":3,   # ~360 arrondissements
}

# Projection recommandée pour le Cameroun
# EPSG:32632 = UTM zone 32N (mètre, précision locale)
# EPSG:4326  = WGS84 (degrés, pour Folium/Plotly)
CRS_LOCAL  = "EPSG:32632"
CRS_GLOBAL = "EPSG:4326"

# Noms officiels des régions (pour le mapping/normalisation)
REGIONS_OFFICIELLES = [
    "Adamaoua", "Centre", "Est", "Extrême-Nord",
    "Littoral", "Nord", "Nord-Ouest", "Ouest",
    "Sud", "Sud-Ouest"
]


# ─────────────────────────────────────────────────────────────────────────────
# 2. CHARGEMENT DES DONNÉES GÉOGRAPHIQUES
# ─────────────────────────────────────────────────────────────────────────────

def _session_avec_retries(total: int = 4, backoff_factor: float = 1.5) -> requests.Session:
    """
    Session requests qui retente les erreurs de connexion/SSL transitoires
    (ex: ConnectionResetError pendant le handshake TLS, fréquent sur les
    réseaux d'entreprise ou avec certains antivirus qui inspectent le HTTPS).
    """
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=total,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def charger_gadm(niveau: int = 1, forcer_telechargement: bool = False) -> gpd.GeoDataFrame:
    """
    Charge les limites administratives depuis GADM (gadm.org).
    niveau : 0=pays, 1=régions, 2=départements, 3=arrondissements

    Stratégie de cache : le GeoJSON est sauvegardé localement.
    """
    cache_path = DATA_DIR / f"cameroun_gadm_niveau{niveau}.geojson"

    if cache_path.exists() and not forcer_telechargement:
        logger.info(f"[cache] Chargement GADM niveau {niveau} depuis {cache_path}")
        return gpd.read_file(cache_path)

    # URL officielle GADM v4.1
    url = f"https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_CMR_{niveau}.json"
    logger.info(f"[download] Téléchargement GADM niveau {niveau} : {url}")

    try:
        session = _session_avec_retries()
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        gdf = gpd.read_file(io.BytesIO(resp.content))
        gdf.to_file(cache_path, driver="GeoJSON")
        logger.info(f"[ok] {len(gdf)} entités chargées, sauvegardées dans {cache_path}")
        return gdf
    except Exception as e:
        logger.error(
            f"Téléchargement GADM échoué après plusieurs tentatives : {e}\n"
            "  Causes fréquentes : pare-feu, antivirus HTTPS, ou serveur GADM instable.\n"
            "  → Téléchargement manuel : https://gadm.org/download_country.html\n"
            f"    Placez le fichier ici : {cache_path.resolve()}"
        )
        raise


def charger_osm_quartiers(ville: str = "Douala") -> gpd.GeoDataFrame:
    """
    Charge les quartiers/arrondissements urbains depuis OpenStreetMap via Overpass API.
    Utile pour un niveau fin à Douala ou Yaoundé.
    """
    # Requête Overpass : polygones admin de niveau 8-10
    overpass_url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json][timeout:60];
    area["name"="{ville}"]["boundary"="administrative"]->.searchArea;
    (
      relation["boundary"="administrative"]["admin_level"~"^(8|9|10)$"](area.searchArea);
    );
    out geom;
    """
    logger.info(f"[osm] Requête Overpass pour {ville}...")
    try:
        resp = requests.post(overpass_url, data=query, timeout=90)
        data = resp.json()
        
        # Conversion en GeoDataFrame
        from shapely.geometry import Polygon, MultiPolygon
        rows = []
        for el in data.get("elements", []):
            if el["type"] == "relation" and "members" in el:
                nom = el.get("tags", {}).get("name", "Inconnu")
                niveau = el.get("tags", {}).get("admin_level", "?")
                # Reconstruction du polygone (simplifié)
                coords = []
                for m in el["members"]:
                    if m.get("role") == "outer" and "geometry" in m:
                        coords.extend([(p["lon"], p["lat"]) for p in m["geometry"]])
                if len(coords) >= 3:
                    try:
                        geom = Polygon(coords)
                        rows.append({"nom": nom, "niveau_admin": niveau, "geometry": geom})
                    except Exception:
                        pass
        
        if not rows:
            logger.info(f"[osm] Aucun quartier trouvé pour {ville}. Vérifiez la requête Overpass.")
            return gpd.GeoDataFrame()
        
        gdf = gpd.GeoDataFrame(rows, crs=CRS_GLOBAL)
        cache_path = DATA_DIR / f"osm_{ville.lower()}_quartiers.geojson"
        gdf.to_file(cache_path, driver="GeoJSON")
        logger.info(f"[ok] {len(gdf)} quartiers OSM chargés pour {ville}")
        return gdf
    except Exception as e:
        logger.error(f"[erreur] OSM Overpass : {e}")
        return gpd.GeoDataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# 3. NORMALISATION DES NOMS (défi majeur !)
# ─────────────────────────────────────────────────────────────────────────────
# normaliser_nom() est importée depuis adapter_dataset.py (source unique)


def preparer_colonne_jointure(
    df: pd.DataFrame,
    col_nom_zone: str,
    col_cible: str = "nom_normalise",
    candidats: list = None
) -> pd.DataFrame:
    """
    Ajoute une colonne de noms normalisés dans le DataFrame métrique
    pour faciliter la jointure avec le GeoDataFrame.
    """
    if candidats is None:
        candidats = REGIONS_OFFICIELLES
    df = df.copy()
    df[col_cible] = df[col_nom_zone].apply(
        lambda x: normaliser_nom(x, candidats)
    )
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 4. JOINTURE GÉOGRAPHIQUE ↔ MÉTRIQUE
# ─────────────────────────────────────────────────────────────────────────────

def joindre_metrique(
    gdf: gpd.GeoDataFrame,
    df_metrique: pd.DataFrame,
    col_geo: str,          # colonne de nom dans le GeoDataFrame
    col_metrique: str,     # colonne de nom dans le DataFrame
    col_valeur: str,       # colonne de la métrique à visualiser
    fuzzy: bool = True
) -> gpd.GeoDataFrame:
    """
    Joint un GeoDataFrame aux données métriques.
    Gère les noms incohérents via fuzzy matching si fuzzy=True.

    Retourne un GeoDataFrame avec la colonne 'valeur' et 'manquant', plus
    une piste de confiance par zone ('_nom_origine', '_match_score',
    '_match_methode') consommée par CarteCameroun.valider().

    Si deux noms d'origine distincts se résolvent vers la même zone
    géographique (ex: "Nord" et "Nord-Ouest" matchant tous deux
    "Nord-Ouest"), les valeurs sont moyennées et la collision est
    signalée — sans ça, le merge gauche dupliquerait silencieusement
    la zone géo dans le résultat.
    """
    gdf = gdf.copy()
    df = df_metrique.copy()

    if fuzzy:
        noms_geo = gdf[col_geo].dropna().unique().tolist()
        matches = df[col_metrique].apply(lambda x: normaliser_nom_avec_score(x, noms_geo))
        df["_merge_col"]     = matches.apply(lambda d: d["nom_normalise"])
        df["_match_score"]   = matches.apply(lambda d: d["score"])
        df["_match_methode"] = matches.apply(lambda d: d["methode"])
        df["_nom_origine"]   = df[col_metrique]
        merge_right_on = "_merge_col"

        # Collisions : plusieurs noms d'origine distincts → même zone géo
        collisions = df.groupby("_merge_col")["_nom_origine"].agg(lambda s: sorted(set(map(str, s))))
        for zone_geo, noms in collisions.items():
            if len(noms) > 1:
                print(
                    f"[join][ALERTE] '{zone_geo}' reçoit des données de {len(noms)} noms "
                    f"d'origine différents : {noms} — valeurs moyennées. "
                    f"Vérifiez avec carte.valider()."
                )

        if df["_merge_col"].duplicated().any():
            agg = df.groupby("_merge_col", as_index=False).agg(
                **{
                    col_valeur: (col_valeur, "mean"),
                    "_match_score": ("_match_score", "min"),
                    "_match_methode": ("_match_methode", "first"),
                    "_nom_origine": ("_nom_origine", lambda s: " / ".join(sorted(set(map(str, s))))),
                }
            )
            df = agg
    else:
        merge_right_on = col_metrique
        df["_match_score"]   = 100
        df["_match_methode"] = "exact_only"
        df["_nom_origine"]   = df[col_metrique]

    # Jointure gauche : toutes les zones géographiques sont conservées
    result = gdf.merge(
        df[[merge_right_on, col_valeur, "_nom_origine", "_match_score", "_match_methode"]],
        left_on=col_geo,
        right_on=merge_right_on,
        how="left"
    )
    result = result.rename(columns={col_valeur: "valeur"})
    result["manquant"] = result["valeur"].isna()

    n_manquant = result["manquant"].sum()
    if n_manquant > 0:
        zones = result.loc[result["manquant"], col_geo].tolist()
        logger.warning(f"[join] {n_manquant} zone(s) sans données : {zones}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 5. SCHÉMAS DE COULEUR & CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

SCHEMAS_COULEUR = {
    # ── Palettes matplotlib (noms) ─────────────────────────────────────────────
    "sequentiel":   "YlOrRd",
    "divergent":    "RdYlGn",
    "qualitatif":   "Set2",
    "population":   "PuBuGn",
    "pauvrete":     "OrRd",
    "sante":        "YlGn",
    "education":    "Blues",
    # ── Palettes corporate (listes hex — utilisables via Style(palette="chaleur")) ──
    "chaleur":     ["#FDEBD0", "#F0B27A", "#E67E22", "#CA6F1E", "#784212"],
    "froid":       ["#EAF4FB", "#AED6F1", "#3498DB", "#1A5276", "#0D2B45"],
    "corporatif":  ["#F2F3F4", "#ABB2B9", "#566573", "#2C3E50", "#17202A"],
    "impact":      ["#FDEDEC", "#F1948A", "#E74C3C", "#922B21", "#641E16"],
    "vert":        ["#EAFAF1", "#A9DFBF", "#27AE60", "#1E8449", "#145A32"],
}

METHODES_CLASSIFICATION = ["quantile", "jenks", "égale", "std"]


def _formater_valeur(v: float) -> str:
    """Formate un nombre pour la légende : 1 234 567 → '1.2M', 12 345 → '12K'."""
    av = abs(v)
    if av >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if av >= 10_000:
        return f"{v / 1_000:.0f}K"
    if av >= 1_000:
        return f"{v / 1_000:.1f}K"
    if 0 < av < 1:
        return f"{v:.2f}"
    return f"{v:.1f}"


def classifier_valeurs(
    series: pd.Series,
    methode: str = "quantile",
    n_classes: int = 5
) -> tuple:
    """
    Classe une série de valeurs selon la méthode choisie.
    Retourne (bornes, étiquettes).
    
    - quantile : classes avec effectif égal (recommandé si distribution asymétrique)
    - jenks    : ruptures naturelles (nécessite mapclassify)
    - égale    : intervalles de même largeur
    - std      : basé sur l'écart-type
    """
    valeurs = series.dropna()

    # Cas dégénéré : une seule zone ou toutes valeurs identiques → une seule classe
    if len(valeurs) == 0:
        raise ValueError("Impossible de classifier une série vide.")
    if valeurs.nunique() == 1:
        v = valeurs.iloc[0]
        bornes = np.array([v - 0.001, v + 0.001])
        return bornes, [f"{v:.1f}"]

    # Réduire n_classes si moins de zones que de classes demandées
    n_classes = min(n_classes, len(valeurs))

    if methode == "quantile":
        bornes = valeurs.quantile(np.linspace(0, 1, n_classes + 1)).unique()
    elif methode == "jenks":
        try:
            import mapclassify
            jnb = mapclassify.NaturalBreaks(valeurs, k=n_classes)
            bornes = np.concatenate([[valeurs.min()], jnb.bins])
        except ImportError:
            logger.warning("[warn] mapclassify non installé → bascule sur quantile")
            bornes = valeurs.quantile(np.linspace(0, 1, n_classes + 1)).unique()
    elif methode == "égale":
        bornes = np.linspace(valeurs.min(), valeurs.max(), n_classes + 1)
    elif methode == "std":
        mu, sigma = valeurs.mean(), valeurs.std()
        bornes = np.array([
            valeurs.min(), mu - 2*sigma, mu - sigma,
            mu, mu + sigma, mu + 2*sigma, valeurs.max()
        ])
        bornes = bornes[(bornes >= valeurs.min()) & (bornes <= valeurs.max())]
        bornes = np.unique(bornes)
    else:
        raise ValueError(f"Méthode inconnue : {methode}. Choisir parmi {METHODES_CLASSIFICATION}")
    
    bornes[0]  -= 0.001   # inclure la valeur minimale
    bornes[-1] += 0.001   # inclure la valeur maximale
    
    etiquettes = [
        f"{_formater_valeur(bornes[i])} – {_formater_valeur(bornes[i+1])}"
        for i in range(len(bornes) - 1)
    ]
    return bornes, etiquettes


# ─────────────────────────────────────────────────────────────────────────────
# 6. HELPERS VISUELS
# ─────────────────────────────────────────────────────────────────────────────

def _appliquer_typographie(s: "Style"):
    """Applique une typographie cohérente à la figure courante via rcParams."""
    famille = s.police or "DejaVu Sans"
    plt.rcParams.update({
        "font.family":      "sans-serif",
        "font.sans-serif":  [famille, "Arial", "Helvetica", "Liberation Sans"],
        "font.size":        s.taille_legende,
        "axes.titlesize":   s.taille_titre,
        "axes.titleweight": "bold",
        "legend.fontsize":  s.taille_legende,
        "figure.dpi":       96,
    })


def _construire_legende(ax, patches, style: "Style" = None):
    """Légende compacte, horizontale, sans titre redondant."""
    s = style or Style()
    n = len(patches)
    ncol = min(n, 5)
    leg = ax.legend(
        handles=patches,
        loc="lower left",
        bbox_to_anchor=(0.01, 0.01),
        ncol=ncol,
        fontsize=s.taille_legende,
        frameon=True,
        framealpha=0.92,
        edgecolor="#DDDDDD",
        fancybox=False,
        handlelength=1.0,
        handleheight=0.75,
        borderpad=0.5,
        labelspacing=0.25,
        columnspacing=0.8,
    )
    leg.get_frame().set_linewidth(0.4)
    return leg


def _ajouter_bordure(ax):
    """Cadre fin #CCCCCC autour de la zone cartographique."""
    rect = plt.Rectangle(
        (0, 0), 1, 1,
        transform=ax.transAxes,
        fill=False, edgecolor="#CCCCCC", linewidth=0.8, zorder=20,
    )
    ax.add_patch(rect)


def _ajouter_message_cle(ax, message: str, style: "Style" = None):
    """
    Bandeau insight McKinsey : fond coloré pleine largeur + barre d'accent + texte bold.
    Placé juste au-dessus de la carte (y > 1 en coordonnées axes, clip_on=False).
    """
    s = style or Style()
    y0, h = 1.013, 0.046
    # Fond pleine largeur
    ax.add_patch(plt.Rectangle(
        (0, y0), 1, h,
        transform=ax.transAxes, clip_on=False,
        facecolor="#EAF4FB", edgecolor="none", zorder=15,
    ))
    # Barre d'accent gauche
    ax.add_patch(plt.Rectangle(
        (0, y0), 0.006, h,
        transform=ax.transAxes, clip_on=False,
        facecolor="#1A5276", edgecolor="none", zorder=16,
    ))
    ax.text(
        0.013, y0 + h / 2, message,
        transform=ax.transAxes, clip_on=False, zorder=17,
        fontsize=s.taille_sous_titre, color="#1A3A4A", fontweight="bold",
        fontfamily=s.police or "sans-serif", va="center", ha="left",
    )


def _resoudre_palette(style: "Style", schema_couleur: str, n: int):
    """Résout la palette : Style.palette (liste ou clé) > schema_couleur matplotlib."""
    s = style or Style()
    raw = s.palette
    if raw is None:
        return plt.get_cmap(schema_couleur, n)
    if isinstance(raw, str):
        lookup = SCHEMAS_COULEUR.get(raw)
        if isinstance(lookup, list):
            raw = lookup
        elif lookup:
            return plt.get_cmap(lookup, n)
        else:
            raise ValueError(
                f"Palette inconnue : '{raw}'. "
                f"Valeurs disponibles : {list(SCHEMAS_COULEUR.keys())}"
            )
    return LinearSegmentedColormap.from_list("custom", raw, N=n)


# ─────────────────────────────────────────────────────────────────────────────
# 7. CARTE STATIQUE — Matplotlib / GeoPandas
# ─────────────────────────────────────────────────────────────────────────────

def carte_statique(
    gdf: gpd.GeoDataFrame,
    titre: str = "Carte du Cameroun",
    col_valeur: str = "valeur",
    col_nom: str = None,
    schema_couleur: str = "YlOrRd",
    methode_classification: str = "quantile",
    n_classes: int = 5,
    unite: str = "",
    afficher_labels: bool = False,
    afficher_contour_pays: bool = True,
    sortie: str = None,
    figsize: tuple = (12, 14),
    # ── Personnalisation ─────────────────────────────────────────────────────
    style: "Style" = None,
    sous_titre: str = None,
    source: str = None,
    message_cle: str = None,
    couches: list = None,
) -> plt.Figure:
    """
    Génère une carte choroplèthe statique de haute qualité.
    
    Paramètres :
        gdf                    : GeoDataFrame avec colonne 'valeur' et 'manquant'
        titre                  : titre de la carte
        col_valeur             : nom de la colonne de données
        col_nom                : colonne avec les noms de zones (pour les labels)
        schema_couleur         : palette matplotlib
        methode_classification : 'quantile', 'jenks', 'égale', 'std'
        n_classes              : nombre de classes
        unite                  : unité affichée dans la légende
        afficher_labels        : écrire le nom de chaque zone
        afficher_contour_pays  : superposer le contour national
        sortie                 : chemin de sauvegarde (PNG/SVG)
        figsize                : taille de la figure
    """
    s = style or Style()
    _appliquer_typographie(s)

    # ── Reprojection ─────────────────────────────────────────────────────────
    gdf_proj = gdf.to_crs(CRS_LOCAL)

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    fig.patch.set_facecolor(s.fond_figure)
    ax.set_facecolor(s.fond_carte)

    # ── Zones sans données : hachurage ───────────────────────────────────────
    gdf_manquant = gdf_proj[gdf_proj["manquant"]]
    if not gdf_manquant.empty:
        gdf_manquant.plot(
            ax=ax, color=s.manquant_couleur,
            edgecolor=s.contours_zones_couleur,
            linewidth=s.contours_zones_epaisseur,
            hatch=s.manquant_hatch,
        )

    # ── Classification & palette ─────────────────────────────────────────────
    gdf_data = gdf_proj[~gdf_proj["manquant"]].copy()

    if not gdf_data.empty:
        valeurs = gdf_data[col_valeur]
        bornes, etiquettes = classifier_valeurs(valeurs, methode_classification, n_classes)
        cmap = _resoudre_palette(s, schema_couleur, len(bornes) - 1)
        norm = BoundaryNorm(bornes, cmap.N)

        gdf_data.plot(
            ax=ax, column=col_valeur, cmap=cmap, norm=norm,
            edgecolor=s.contours_zones_couleur,
            linewidth=s.contours_zones_epaisseur,
            legend=False,
        )

        # ── Légende compacte ─────────────────────────────────────────────────
        patches = [
            mpatches.Patch(
                facecolor=cmap(i / max(len(etiquettes) - 1, 1)),
                edgecolor="#AAAAAA", linewidth=0.3,
                label=f"{etiq}{(' ' + unite) if unite else ''}",
            )
            for i, etiq in enumerate(etiquettes)
        ]
        if not gdf_manquant.empty:
            patches.append(mpatches.Patch(
                facecolor=s.manquant_couleur, hatch=s.manquant_hatch,
                edgecolor="#AAAAAA", linewidth=0.3, label="N/D",
            ))
        _construire_legende(ax, patches, s)

    # ── Contour pays ─────────────────────────────────────────────────────────
    if afficher_contour_pays:
        gdf_proj.boundary.plot(
            ax=ax,
            color=s.contours_pays_couleur,
            linewidth=s.contours_pays_epaisseur,
            alpha=0.5,
        )

    # ── Labels des zones ─────────────────────────────────────────────────────
    if afficher_labels and col_nom:
        for _, row in gdf_proj.iterrows():
            if row.geometry is None:
                continue
            centroid = row.geometry.centroid
            nom = str(row.get(col_nom, ""))
            if nom and nom != "nan":
                ax.annotate(
                    nom, xy=(centroid.x, centroid.y),
                    ha="center", va="center",
                    fontsize=s.taille_labels, color="#222222",
                    fontweight="bold",
                    fontfamily=s.police or "sans-serif",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              alpha=0.6, ec="none"),
                )

    # ── Couches superposées (symboles + annotations) ──────────────────────────
    if couches:
        _rendre_couches(ax, couches, gdf_proj, col_nom, CRS_LOCAL)

    # ── Titre ────────────────────────────────────────────────────────────────
    # pad agrandi quand message_cle est présent pour laisser de la place au bandeau
    titre_pad = 52 if message_cle else 14
    ax.set_title(
        titre,
        fontsize=s.taille_titre, fontweight="bold", pad=titre_pad,
        color=s.couleur_titre,
        fontfamily=s.police or "sans-serif",
    )
    if sous_titre:
        y_st = 1.075 if message_cle else 1.005
        ax.text(
            0.5, y_st, sous_titre,
            transform=ax.transAxes,
            fontsize=s.taille_sous_titre, color="#555555",
            ha="center", va="bottom",
            fontfamily=s.police or "sans-serif",
        )
    if message_cle:
        _ajouter_message_cle(ax, message_cle, s)

    ax.axis("off")
    if s.bordure_carte:
        _ajouter_bordure(ax)

    # ── Rose des vents ────────────────────────────────────────────────────────
    ax.annotate("N", xy=(0.97, 0.97), xycoords="axes fraction",
                fontsize=12, ha="center", va="center", fontweight="bold",
                color="#444444")
    ax.annotate("↑", xy=(0.97, 0.94), xycoords="axes fraction",
                fontsize=18, ha="center", va="center", color="#444444")

    # ── Barre d'échelle ───────────────────────────────────────────────────────
    _ajouter_echelle(ax, gdf_proj)

    # ── Source ────────────────────────────────────────────────────────────────
    texte_source = source if source is not None else "Source : GADM v4.1"
    ax.text(0.01, 0.01, texte_source,
            transform=ax.transAxes, fontsize=s.taille_source, color="#999999")

    plt.tight_layout(pad=1.2)

    if sortie:
        fig.savefig(sortie, dpi=s.dpi, bbox_inches="tight",
                    pad_inches=0.15, facecolor=fig.get_facecolor())
        logger.info(f"[ok] Carte sauvegardée : {sortie}")

    return fig


def _ajouter_echelle(ax, gdf_proj):
    """Ajoute une barre d'échelle approximative."""
    try:
        xmin, ymin, xmax, ymax = gdf_proj.total_bounds
        largeur = (xmax - xmin) * 0.2
        cible_km = 200
        # En UTM zone 32N, 1 unité = 1 mètre
        longueur_barre = cible_km * 1000
        x0 = xmin + (xmax - xmin) * 0.05
        y0 = ymin + (ymax - ymin) * 0.04
        ax.plot([x0, x0 + longueur_barre], [y0, y0],
                color="#333333", linewidth=2)
        ax.text(x0 + longueur_barre / 2, y0 * 1.008,
                f"{cible_km} km", ha="center", va="bottom", fontsize=7)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 6b. RENDU DES COUCHES SUPERPOSÉES (symboles + annotations)
# ─────────────────────────────────────────────────────────────────────────────

# Catalogue des symboles prédéfinis disponibles
SYMBOLES_PREDEFINIS = {
    "cercle":    "o",  "carre":    "s",  "triangle": "^",
    "losange":   "D",  "etoile":   "*",  "croix":    "P",
    "x":         "X",  "fleche":   ">",  "plus":     "+",
    "★": "★", "▲": "▲", "●": "●", "■": "■", "✚": "✚", "⚠": "⚠",
}


def _coords_zone(nom_zone: str, gdf_proj: gpd.GeoDataFrame, col_nom: str):
    """Retourne (x, y) projeté du centroïde d'une zone par son nom."""
    masque = gdf_proj[col_nom].str.lower() == nom_zone.lower()
    if not masque.any():
        # Tentative de matching souple
        candidats = gdf_proj[col_nom].dropna().tolist()
        nom_corr = normaliser_nom(nom_zone, candidats)
        masque = gdf_proj[col_nom].str.lower() == nom_corr.lower()
    if not masque.any():
        raise ValueError(f"Zone '{nom_zone}' introuvable dans la géométrie.")
    centroid = gdf_proj[masque].geometry.centroid.iloc[0]
    return centroid.x, centroid.y


def _coords_latlon(lat: float, lon: float, crs_proj: str):
    """Convertit lat/lon WGS84 en coordonnées projetées."""
    pt = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([lon], [lat]), crs="EPSG:4326"
    ).to_crs(crs_proj)
    return pt.geometry.iloc[0].x, pt.geometry.iloc[0].y


def _rendre_couches(
    ax,
    couches: list,
    gdf_proj: gpd.GeoDataFrame,
    col_nom: str,
    crs_proj: str,
):
    """Dessine toutes les couches (symboles + annotations) sur l'axe matplotlib."""
    for couche in couches:
        if couche["type"] == "symboles":
            _rendre_couche_symboles(ax, couche, gdf_proj, col_nom, crs_proj)
        elif couche["type"] == "annotation":
            _rendre_couche_annotation(ax, couche, gdf_proj, col_nom, crs_proj)
        elif couche["type"] == "symboles_prop":
            _rendre_couche_symboles_prop(ax, couche, gdf_proj, col_nom, crs_proj)


def _rendre_couche_symboles(ax, couche, gdf_proj, col_nom, crs_proj):
    df        = couche["df"]
    mode      = couche["mode"]         # "zone" | "coordonnees"
    sym_type  = couche["sym_type"]     # "predefined" | "image"
    col_cat   = couche.get("col_categorie")
    symboles  = couche.get("symboles", {})
    couleurs  = couche.get("couleurs", {})
    couleur_d = couche.get("couleur", "#333333")
    symbole_d = couche.get("symbole", "o")
    taille    = couche.get("taille", 12)
    alpha     = couche.get("alpha", 0.9)
    zorder    = couche.get("zorder", 5)
    offset    = couche.get("offset", (0, 0))

    for _, row in df.iterrows():
        # ── Coordonnées du point ─────────────────────────────────────────────
        if mode == "zone":
            try:
                x, y = _coords_zone(str(row[couche["col_zone"]]), gdf_proj, col_nom)
            except ValueError:
                continue
        else:
            x, y = _coords_latlon(float(row[couche["col_lat"]]),
                                   float(row[couche["col_lon"]]), crs_proj)

        # ── Catégorie ────────────────────────────────────────────────────────
        cat = str(row[col_cat]) if col_cat and col_cat in row.index else None
        couleur = couleurs.get(cat, couleur_d) if cat else couleur_d
        sym     = symboles.get(cat, symbole_d) if cat else symbole_d

        # ── Rendu ────────────────────────────────────────────────────────────
        if sym_type == "image":
            chemin = symboles.get(cat) if cat else sym
            try:
                import PIL.Image as PILImage
                img = PILImage.open(chemin).convert("RGBA")
                img = img.resize((taille * 3, taille * 3), PILImage.LANCZOS)
                oi  = OffsetImage(np.array(img), zoom=1.0, alpha=alpha)
                ab  = AnnotationBbox(
                    oi, (x, y), xybox=(x + offset[0], y + offset[1]),
                    frameon=False, zorder=zorder,
                )
                ax.add_artist(ab)
            except Exception as e:
                logger.error(f"[symboles] Impossible de charger l'image '{chemin}' : {e}")
        else:
            # Symbole unicode → ax.text ; marqueur matplotlib → ax.plot
            if sym in SYMBOLES_PREDEFINIS and SYMBOLES_PREDEFINIS[sym] == sym:
                # C'est un caractère unicode direct (★, ▲, ●…)
                ax.text(x + offset[0], y + offset[1], sym,
                        fontsize=taille, color=couleur, ha="center", va="center",
                        alpha=alpha, zorder=zorder,
                        fontfamily="DejaVu Sans")
            else:
                marker = SYMBOLES_PREDEFINIS.get(sym, sym)
                ax.plot(x + offset[0], y + offset[1], marker=marker,
                        color=couleur, markersize=taille / 1.5, alpha=alpha,
                        zorder=zorder, linestyle="None",
                        markeredgecolor="white", markeredgewidth=0.4)


def _rendre_couche_annotation(ax, couche, gdf_proj, col_nom, crs_proj):
    cible       = couche["cible"]
    texte       = couche["texte"]
    style_ann   = couche.get("style_ann", "callout")
    fond        = couche.get("couleur_fond", "#FFFDE7")
    coul_texte  = couche.get("couleur_texte", "#1A1A1A")
    coul_fleche = couche.get("couleur_fleche", "#333333")
    taille      = couche.get("taille_texte", 8)
    offset      = couche.get("offset", (30, 30))
    zorder      = couche.get("zorder", 6)

    # ── Coordonnées cible ────────────────────────────────────────────────────
    if isinstance(cible, str):
        try:
            x, y = _coords_zone(cible, gdf_proj, col_nom)
        except ValueError:
            logger.info(f"[annotation] Zone '{cible}' introuvable — ignorée.")
            return
    else:
        # (lon, lat) tuple
        x, y = _coords_latlon(float(cible[1]), float(cible[0]), crs_proj)

    bbox_props = dict(
        boxstyle="round,pad=0.4",
        facecolor=fond,
        edgecolor="#AAAAAA",
        linewidth=0.6,
        alpha=0.93,
    )

    if style_ann == "callout":
        ax.annotate(
            texte,
            xy=(x, y),
            xytext=(x + offset[0] * 1000, y + offset[1] * 1000),
            fontsize=taille, color=coul_texte, zorder=zorder,
            bbox=bbox_props,
            arrowprops=dict(
                arrowstyle="-|>",
                color=coul_fleche,
                lw=0.8,
                connectionstyle="arc3,rad=0.15",
            ),
        )
    elif style_ann == "highlight":
        ax.annotate(
            texte,
            xy=(x, y),
            fontsize=taille, color=coul_texte, zorder=zorder,
            ha="center", va="center",
            bbox=bbox_props,
        )
    else:  # "valeur" — texte simple sans boîte
        ax.text(x, y, texte, fontsize=taille, color=coul_texte,
                ha="center", va="center", zorder=zorder, fontweight="bold")


def _rendre_couche_symboles_prop(ax, couche, gdf_proj, col_nom, crs_proj):
    """
    Dessine des cercles proportionnels centrés sur chaque zone.
    Rayon ∝ sqrt(valeur) pour que l'AIRE soit proportionnelle à la valeur.
    """
    col_valeur   = couche["col_valeur"]
    couleur      = couche.get("couleur", "#E74C3C")
    alpha        = couche.get("alpha", 0.55)
    echelle      = couche.get("echelle", 1.0)
    afficher_val = couche.get("afficher_valeur", True)
    zorder       = couche.get("zorder", 7)

    gdf_work = gdf_proj.copy()
    if col_valeur not in gdf_work.columns:
        logger.warning(f"[warn] symboles_prop : colonne '{col_valeur}' absente du GeoDataFrame")
        return

    valeurs = gdf_work[col_valeur].dropna()
    if valeurs.empty:
        return

    v_max = valeurs.max()
    if v_max == 0:
        return

    # Rayon max exprimé en unités de projection (mètres en UTM32N)
    xmin, ymin, xmax, ymax = gdf_work.total_bounds
    r_max = (xmax - xmin) * 0.06 * echelle  # 6% de la largeur de la carte

    for _, row in gdf_work.iterrows():
        if row.geometry is None or pd.isna(row.get(col_valeur)):
            continue
        v = row[col_valeur]
        if v <= 0:
            continue
        r = r_max * np.sqrt(v / v_max)
        cx, cy = row.geometry.centroid.x, row.geometry.centroid.y
        circle = plt.Circle(
            (cx, cy), r,
            facecolor=couleur, edgecolor="white",
            linewidth=0.8, alpha=alpha, zorder=zorder,
        )
        ax.add_patch(circle)
        if afficher_val:
            label = _formater_valeur(v)
            ax.text(
                cx, cy, label,
                ha="center", va="center",
                fontsize=6.5, color="white", fontweight="bold",
                zorder=zorder + 1,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 7. CARTE INTERACTIVE — Folium (HTML exportable)
# ─────────────────────────────────────────────────────────────────────────────

def carte_interactive_folium(
    gdf: gpd.GeoDataFrame,
    titre: str = "Cameroun — Carte interactive",
    col_valeur: str = "valeur",
    col_nom: str = None,
    col_tooltip: list = None,
    schema_couleur: str = "YlOrRd",
    methode_classification: str = "quantile",
    n_classes: int = 5,
    unite: str = "",
    sortie: str = "carte_cameroun.html"
) -> folium.Map:
    """
    Génère une carte choroplèthe interactive avec Folium.
    Exportable en HTML autonome, utilisable hors ligne.
    
    col_tooltip : liste de colonnes à afficher dans l'infobulle
    """
    # Toujours en WGS84 pour Folium
    gdf_wgs = gdf.to_crs(CRS_GLOBAL)
    
    # Centre sur le Cameroun
    centre = [5.5, 12.5]
    m = folium.Map(location=centre, zoom_start=6,
                   tiles="CartoDB positron",
                   prefer_canvas=True)
    
    # Titre
    titre_html = f"""
    <div style="position: fixed; top: 10px; left: 50%; transform: translateX(-50%);
                z-index: 1000; background: white; padding: 8px 18px;
                border-radius: 4px; box-shadow: 0 1px 6px rgba(0,0,0,0.15);
                font-family: sans-serif; font-size: 15px; font-weight: bold;">
        {titre}
    </div>"""
    m.get_root().html.add_child(folium.Element(titre_html))
    
    # ── Classification ────────────────────────────────────────────────────────
    gdf_data = gdf_wgs[~gdf_wgs["manquant"]]
    
    if not gdf_data.empty:
        valeurs = gdf_data[col_valeur]
        bornes, etiquettes = classifier_valeurs(valeurs, methode_classification, n_classes)
        
        # Construction de la palette (hex)
        cmap = plt.get_cmap(schema_couleur, n_classes)
        couleurs_hex = [
            "#%02x%02x%02x" % tuple(int(c * 255) for c in cmap(i / max(n_classes - 1, 1))[:3])
            for i in range(n_classes)
        ]
        
        def couleur_pour_valeur(val):
            if pd.isna(val):
                return "#CCCCCC"
            for i in range(len(bornes) - 1):
                if bornes[i] <= val < bornes[i + 1]:
                    return couleurs_hex[min(i, len(couleurs_hex) - 1)]
            return couleurs_hex[-1]
        
        # ── Colonnes tooltip ──────────────────────────────────────────────────
        if col_tooltip is None:
            col_tooltip = []
            if col_nom:
                col_tooltip.append(col_nom)
            col_tooltip.append(col_valeur)
        
        # ── Ajout des polygones ───────────────────────────────────────────────
        for _, row in gdf_wgs.iterrows():
            if row.geometry is None:
                continue
            
            val  = row.get(col_valeur, None)
            coul = "#CCCCCC" if pd.isna(val) else couleur_pour_valeur(val)
            
            # Contenu du tooltip
            contenu_tooltip = ""
            if col_nom and col_nom in row.index:
                contenu_tooltip += f"<b>{row[col_nom]}</b><br>"
            if not pd.isna(val):
                contenu_tooltip += f"{col_valeur} : {val:.2f} {unite}"
            else:
                contenu_tooltip += "<i>Donnée manquante</i>"
            
            folium.GeoJson(
                row.geometry.__geo_interface__,
                style_function=lambda x, c=coul: {
                    "fillColor":   c,
                    "color":       "#FFFFFF",
                    "weight":      0.8,
                    "fillOpacity": 0.75,
                },
                highlight_function=lambda x: {
                    "fillOpacity": 0.95,
                    "weight":      2,
                    "color":       "#333333"
                },
                tooltip=folium.Tooltip(contenu_tooltip, sticky=True),
            ).add_to(m)
        
        # ── Légende ───────────────────────────────────────────────────────────
        legende_html = """
        <div style="position: fixed; bottom: 30px; left: 20px; z-index: 1000;
                    background: white; padding: 12px 16px; border-radius: 4px;
                    box-shadow: 0 1px 6px rgba(0,0,0,0.15);
                    font-family: sans-serif; font-size: 12px;">
        <b style="display:block; margin-bottom:6px;">Légende</b>"""
        
        for i, (etiq, coul) in enumerate(zip(etiquettes, couleurs_hex)):
            legende_html += f"""
            <div style="display:flex; align-items:center; margin-bottom:3px;">
                <span style="width:18px; height:14px; background:{coul};
                             border:0.5px solid #ccc; display:inline-block;
                             margin-right:7px; border-radius:2px;"></span>
                {etiq} {unite}
            </div>"""
        
        legende_html += """
            <div style="display:flex; align-items:center; margin-top:4px;">
                <span style="width:18px; height:14px; background:#CCCCCC;
                             border:0.5px solid #ccc; display:inline-block;
                             margin-right:7px; border-radius:2px;"></span>
                Données manquantes
            </div>
        </div>"""
        
        m.get_root().html.add_child(folium.Element(legende_html))
    
    # ── Fonds de carte alternatifs ────────────────────────────────────────────
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap").add_to(m)
    folium.TileLayer("CartoDB dark_matter", name="Sombre").add_to(m)
    folium.LayerControl().add_to(m)
    
    if sortie:
        m.save(sortie)
        logger.info(f"[ok] Carte interactive sauvegardée : {sortie}")
    
    return m


# ─────────────────────────────────────────────────────────────────────────────
# 8. CARTE INTERACTIVE — Plotly Express (intégrable dans Dash/Streamlit)
# ─────────────────────────────────────────────────────────────────────────────

def carte_plotly(
    gdf: gpd.GeoDataFrame,
    titre: str = "Cameroun",
    col_valeur: str = "valeur",
    col_nom: str = None,
    col_id: str = None,
    schema_couleur: str = "YlOrRd",
    unite: str = "",
    sortie: str = None
):
    """
    Carte choroplèthe interactive avec Plotly Express.
    Idéal pour intégration dans une app Streamlit ou Dash.
    
    col_id : identifiant unique de chaque zone (optionnel)
    """
    import plotly.express as px
    
    gdf_wgs = gdf.to_crs(CRS_GLOBAL).copy()
    
    if col_id is None:
        gdf_wgs["_id"] = gdf_wgs.index.astype(str)
        col_id = "_id"
    
    geojson = gdf_wgs.__geo_interface__
    
    # Ajout de l'id aux features pour le mapping
    for i, feat in enumerate(geojson["features"]):
        feat["id"] = str(gdf_wgs.iloc[i][col_id])
    
    hover_data = {}
    if col_nom:
        hover_data[col_nom] = True
    
    fig = px.choropleth(
        gdf_wgs,
        geojson=geojson,
        locations=col_id,
        color=col_valeur,
        color_continuous_scale=schema_couleur,
        title=titre,
        labels={col_valeur: unite or col_valeur},
        hover_name=col_nom,
        hover_data=hover_data,
        fitbounds="locations",
        basemap_visible=False,
    )
    
    fig.update_layout(
        margin={"r": 0, "t": 40, "l": 0, "b": 0},
        paper_bgcolor="#F8F8F0",
        geo=dict(
            bgcolor="#D4E8F4",
            showcoastlines=True, coastlinecolor="#AAAAAA",
            showland=True, landcolor="#F8F8F0",
            showframe=False,
        ),
    )
    
    if sortie:
        fig.write_html(sortie)
        logger.info(f"[ok] Carte Plotly sauvegardée : {sortie}")
    
    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 9. CLASSE PRINCIPALE — Interface unifiée
# ─────────────────────────────────────────────────────────────────────────────

class CarteCameroun:
    """
    Interface principale pour la visualisation cartographique du Cameroun.
    
    Exemple d'utilisation :
    
        carte = CarteCameroun(niveau="regions")
        carte.charger_geo()
        
        df = pd.read_csv("mon_indicateur.csv")
        # df doit contenir : colonne de nom de zone + colonne métrique
        
        carte.charger_metrique(df, col_nom="region", col_valeur="taux_acces_eau")
        
        # Carte statique
        carte.visualiser(
            titre="Accès à l'eau potable par région — Cameroun 2024",
            unite="%",
            methode="quantile",
            sortie="carte_eau.png"
        )
        
        # Carte interactive
        carte.visualiser_interactif(
            titre="Cameroun — Accès à l'eau",
            moteur="folium",   # ou "plotly"
            sortie="carte_eau.html"
        )
    """
    
    def __init__(self, niveau: str = "regions", style: Style = None):
        """
        niveau : 'pays', 'regions', 'departements', 'arrondissements', 'osm_quartiers'
        style  : objet Style pour personnaliser couleurs, police, fonds, DPI
        """
        if niveau not in list(GADM_LEVELS.keys()) + ["osm_quartiers"]:
            raise ValueError(f"Niveau inconnu : {niveau}. Choisir parmi {list(GADM_LEVELS)}")

        self.niveau    = niveau
        self.style     = style or Style()
        self.gdf       = None
        self.gdf_joint = None
        self._col_nom  = None
        self._couches: list = []   # couches superposées accumulées
    
    def __repr__(self) -> str:
        geo = f"zones={len(self.gdf)}" if self.gdf is not None else "geo=non chargée"
        if self.gdf_joint is not None:
            n_ok = int((~self.gdf_joint["manquant"]).sum())
            n_total = len(self.gdf_joint)
            data = f"données={n_ok}/{n_total}"
        else:
            data = "données=non chargées"
        couches = f"couches={len(self._couches)}"
        return f"CarteCameroun(niveau={self.niveau!r}, {geo}, {data}, {couches})"

    def charger_geo(self, ville_osm: str = "Douala", forcer: bool = False):
        """Charge les limites géographiques selon le niveau choisi."""
        if self.niveau == "osm_quartiers":
            self.gdf = charger_osm_quartiers(ville_osm)
            self._col_nom = "nom"
        else:
            niveau_num = GADM_LEVELS[self.niveau]
            self.gdf   = charger_gadm(niveau_num, forcer)
            # GADM noms : NAME_1 (régions), NAME_2 (dépts), etc.
            niv_nom = max(niveau_num, 1)
            self._col_nom = f"NAME_{niv_nom}"
        
        logger.info(f"[ok] {len(self.gdf)} zones chargées pour le niveau '{self.niveau}'")
        return self
    
    def charger_metrique(
        self,
        df: pd.DataFrame,
        col_nom: str = None,
        col_valeur: str = None,
        fuzzy: bool = True,
        auto_adapter: bool = True,
        diagnostic: bool = True
    ):
        """
        Joint les données métriques aux limites géographiques.
        
        df         : DataFrame contenant les données, format quelconque
        col_nom    : colonne avec les noms de zones dans df (déduite si None)
        col_valeur : colonne avec les valeurs métriques dans df (déduite si None)
        fuzzy      : activer le matching approximatif des noms
        auto_adapter : si True, passe le dataset par adapter_dataset() pour
                       gérer automatiquement format large/long, doublons, etc.
                       Mettre à False si votre DataFrame est déjà au format
                       'zone' / 'valeur' propre et que vous voulez éviter
                       toute transformation implicite.
        diagnostic : affiche un rapport diagnostiquer_dataset() avant adaptation
        """
        if self.gdf is None:
            raise CarteCamerounError(
                "La géométrie n'est pas chargée. "
                "Appelez charger_geo() avant cette méthode."
            )
        
        if diagnostic:
            diagnostiquer_dataset(df)
        
        if auto_adapter:
            df_std = adapter_dataset(df, col_zone=col_nom, col_valeur=col_valeur)
            col_nom_utilise   = "zone"
            col_valeur_utilise = "valeur"
        else:
            if not col_nom or not col_valeur:
                raise ValueError(
                    "Avec auto_adapter=False, col_nom et col_valeur sont obligatoires. "
                    "Exemple : charger_metrique(df, col_nom='region', col_valeur='taux')"
                )
            df_std = df
            col_nom_utilise    = col_nom
            col_valeur_utilise = col_valeur
        
        self.gdf_joint = joindre_metrique(
            self.gdf,
            df_std,
            col_geo=self._col_nom,
            col_metrique=col_nom_utilise,
            col_valeur=col_valeur_utilise,
            fuzzy=fuzzy
        )
        self._col_valeur = col_valeur_utilise
        logger.info(f"[ok] Jointure réussie — {(~self.gdf_joint['manquant']).sum()}/{len(self.gdf_joint)} zones avec données")
        return self

    def diagnostiquer(self):
        """Affiche un diagnostic de la jointure et des données."""
        if self.gdf is not None:
            print(f"\n── Géométrie ({self.niveau}) ──────────────────")
            print(f"  Zones        : {len(self.gdf)}")
            print(f"  Colonne nom  : {self._col_nom}")
            print(f"  CRS          : {self.gdf.crs}")
            print(f"  Colonnes     : {list(self.gdf.columns)}")
        
        if self.gdf_joint is not None:
            print(f"\n── Jointure ──────────────────────────────")
            print(f"  Avec données : {(~self.gdf_joint['manquant']).sum()}")
            print(f"  Sans données : {self.gdf_joint['manquant'].sum()}")
            if not self.gdf_joint["manquant"].all():
                print(f"  Min / Max    : {self.gdf_joint['valeur'].min():.2f} / {self.gdf_joint['valeur'].max():.2f}")
                print(f"  Médiane      : {self.gdf_joint['valeur'].median():.2f}")

    def valider(self, seuil_alerte: int = 85) -> pd.DataFrame:
        """
        Affiche une table de correspondance ligne par ligne entre les noms
        d'origine de votre CSV et les zones géographiques sur lesquelles ils
        ont été joints — pour vérifier la jointure au lieu de lui faire
        confiance aveuglément (voir CONTEXTE_PROJET.md, point sur la
        validation du résultat).

        seuil_alerte : score de correspondance fuzzy (0-100) sous lequel une
                       correspondance est signalée comme "à vérifier", même
                       si elle a dépassé le seuil minimal de jointure (70%
                       par défaut dans normaliser_nom).

        Retourne le DataFrame de correspondance (utilisable pour inspection
        ou export), et l'affiche également dans la console.
        """
        if self.gdf_joint is None:
            raise CarteCamerounError(
                "Les données ne sont pas chargées. "
                "Appelez charger_metrique() avant cette méthode."
            )

        colonnes = [self._col_nom, "_nom_origine", "_match_methode", "_match_score", "valeur", "manquant"]
        colonnes = [c for c in colonnes if c in self.gdf_joint.columns]
        table = self.gdf_joint[colonnes].copy()
        table = table.rename(columns={
            self._col_nom: "zone_geo",
            "_nom_origine": "nom_dans_csv",
            "_match_methode": "methode",
            "_match_score": "score",
        })

        n_total   = len(table)
        n_exact   = (table.get("methode") == "exact").sum() if "methode" in table else None
        n_fuzzy   = (table.get("methode") == "fuzzy").sum() if "methode" in table else None
        n_absent  = table["manquant"].sum()
        n_douteux = 0
        if "score" in table.columns:
            n_douteux = ((table["methode"] == "fuzzy") & (table["score"] < seuil_alerte)).sum()

        print(f"\n── Validation de la jointure ({self.niveau}) ───────────────")
        print(f"  Zones totales        : {n_total}")
        if n_exact is not None:
            print(f"  Correspondance exacte : {n_exact}")
            print(f"  Correspondance fuzzy  : {n_fuzzy}")
        print(f"  Sans donnée           : {n_absent}")
        if n_douteux:
            print(f"  [ALERTE] {n_douteux} correspondance(s) fuzzy sous {seuil_alerte}% — à vérifier manuellement :")
            douteux = table[(table["methode"] == "fuzzy") & (table["score"] < seuil_alerte)]
            for _, row in douteux.iterrows():
                print(f"    '{row['nom_dans_csv']}' → '{row['zone_geo']}' ({row['score']}%)")

        print(table.to_string(index=False))
        return table

    # ── ZOOM / FOCUS ─────────────────────────────────────────────────────────

    def focus(self, zone: str, col_parent: str = None) -> "CarteCameroun":
        """
        MODE A — Filtre spatial : restreint la vue à une zone précise
        sans changer de niveau administratif.

        Exemple :
            carte = CarteCameroun(niveau="regions")
            carte.charger_geo().charger_metrique(df)
            carte.focus("Littoral").visualiser(titre="Région Littoral")

        La carte affichera uniquement la région Littoral colorée par la métrique.
        Si vous voulez garder le contexte national en arrière-plan, utilisez
        visualiser() avec afficher_contexte=True (voir ci-dessous).

        zone       : nom de la zone à isoler (ex: "Littoral", "Wouri")
        col_parent : colonne à utiliser pour le filtre (déduite automatiquement si None)
        """
        if self.gdf_joint is None:
            raise CarteCamerounError(
                "Les données ne sont pas chargées. "
                "Appelez charger_metrique() avant cette méthode."
            )

        col = col_parent or self._col_nom
        masque = self.gdf_joint[col].str.lower() == zone.lower()

        if masque.sum() == 0:
            # Tentative de matching approximatif
            valeurs_dispo = self.gdf_joint[col].dropna().unique().tolist()
            try:
                from thefuzz import process as fp
                match, score = fp.extractOne(zone, valeurs_dispo)
                if score >= 70:
                    logger.info(f"[focus] '{zone}' non trouvé → correspondance : '{match}' ({score}%)")
                    masque = self.gdf_joint[col].str.lower() == match.lower()
                else:
                    raise ValueError(f"Zone '{zone}' introuvable. Valeurs disponibles : {valeurs_dispo}")
            except ImportError:
                raise ValueError(
                    f"Zone '{zone}' introuvable. Valeurs disponibles : {valeurs_dispo}\n"
                    "Installez thefuzz pour le matching approximatif : pip install thefuzz"
                )

        clone = CarteCameroun.__new__(CarteCameroun)
        clone.niveau    = self.niveau
        clone._col_nom  = self._col_nom
        clone._col_valeur = self._col_valeur
        clone.gdf       = self.gdf[masque].copy()
        clone.gdf_joint = self.gdf_joint[masque].copy()
        clone._zone_focus = zone
        clone._gdf_contexte = self.gdf_joint  # garde le contexte complet

        logger.info(f"[focus] Zone isolée : '{zone}' — {masque.sum()} entité(s)")
        return clone

    def drill_down(
        self,
        zone: str,
        niveau_interieur: str,
        col_filtre: str = "NAME_1",
        df_metrique: pd.DataFrame = None,
        col_nom_metrique: str = None,
        col_valeur_metrique: str = None,
    ) -> "CarteCameroun":
        """
        MODE B — Drill-down : charge un niveau plus fin (ex: départements)
        à l'intérieur d'une zone parente (ex: une région).

        Exemple :
            carte = CarteCameroun(niveau="regions")
            carte.charger_geo().charger_metrique(df_regions)

            # Zoomer sur le Littoral et afficher ses 4 départements
            zoom = carte.drill_down(
                zone="Littoral",
                niveau_interieur="departements",
                df_metrique=df_departements,
            )
            zoom.visualiser(titre="Départements du Littoral")

        zone              : nom de la zone parente (ex: "Littoral")
        niveau_interieur  : niveau des subdivisions à afficher ('departements',
                            'arrondissements')
        col_filtre        : colonne GADM indiquant la région parente dans le
                            fichier du niveau inférieur (par défaut NAME_1)
        df_metrique       : données métriques pour le niveau intérieur (optionnel)
        """
        niveau_num = GADM_LEVELS.get(niveau_interieur)
        if niveau_num is None:
            raise ValueError(
                f"Niveau inconnu : '{niveau_interieur}'. "
                f"Choisir parmi : {list(GADM_LEVELS.keys())}"
            )

        # Charge la géométrie du niveau inférieur
        gdf_fin = charger_gadm(niveau_num)

        # Filtre sur la zone parente
        if col_filtre not in gdf_fin.columns:
            # Cherche une colonne NAME_x qui correspond
            candidates = [c for c in gdf_fin.columns if c.startswith("NAME_")]
            col_filtre = candidates[0] if candidates else col_filtre

        masque = gdf_fin[col_filtre].str.lower() == zone.lower()
        if masque.sum() == 0:
            valeurs = gdf_fin[col_filtre].dropna().unique().tolist()
            raise ValueError(
                f"Zone parente '{zone}' introuvable dans la colonne '{col_filtre}'.\n"
                f"Valeurs disponibles (extrait) : {valeurs[:10]}"
            )

        gdf_zone = gdf_fin[masque].copy()
        logger.info(f"[drill_down] '{zone}' → {len(gdf_zone)} {niveau_interieur}")

        # Construit un nouveau CarteCameroun sur ce sous-ensemble
        clone = CarteCameroun(niveau=niveau_interieur)
        clone.gdf      = gdf_zone
        niv_num_fin    = niveau_num
        clone._col_nom = f"NAME_{niv_num_fin}"
        clone._zone_focus    = zone
        clone._gdf_contexte  = self.gdf_joint  # contexte = carte parente

        if df_metrique is not None:
            clone.charger_metrique(
                df_metrique,
                col_nom=col_nom_metrique,
                col_valeur=col_valeur_metrique,
            )
        else:
            # Pas de données métriques : crée un gdf_joint minimal sans valeur
            clone.gdf_joint = gdf_zone.copy()
            clone.gdf_joint["valeur"]   = np.nan
            clone.gdf_joint["manquant"] = True
            clone._col_valeur = "valeur"

        return clone

    # ── COUCHES SUPERPOSÉES ───────────────────────────────────────────────────

    def ajouter_symboles(
        self,
        df: pd.DataFrame,
        *,
        type: str = "predefined",
        col_zone: str = None,
        col_lat: str = None,
        col_lon: str = None,
        col_categorie: str = None,
        symboles: dict = None,
        couleurs: dict = None,
        couleur: str = "#333333",
        symbole: str = "o",
        taille: int = 12,
        alpha: float = 0.9,
        offset: tuple = (0, 0),
        zorder: int = 5,
    ) -> "CarteCameroun":
        """
        Ajoute une couche de symboles sur la carte.

        Deux modes de placement (au moins un requis) :
          col_zone  : nom de zone dans df → centroïde automatique
          col_lat + col_lon : coordonnées explicites (WGS84)

        Deux types de symbole :
          "predefined" : marqueur matplotlib (o, s, ^, D, *, P, X)
                         ou unicode (★ ▲ ● ■ ✚ ⚠)
          "image"      : fichier PNG/SVG — dict symboles = {"cat": "chemin.png"}

        Paramètre optionnel col_categorie : colonne du df qui sélectionne
        le symbole/couleur dans les dicts symboles/couleurs.
        Sans col_categorie : tous les points reçoivent symbole/couleur par défaut.

        Retourne self pour permettre le chaînage.
        """
        if col_zone is None and (col_lat is None or col_lon is None):
            raise ValueError(
                "Précisez col_zone OU (col_lat + col_lon) pour placer les symboles."
            )
        mode = "zone" if col_zone else "coordonnees"
        self._couches.append({
            "type":          "symboles",
            "df":            df.copy(),
            "mode":          mode,
            "sym_type":      type,
            "col_zone":      col_zone,
            "col_lat":       col_lat,
            "col_lon":       col_lon,
            "col_categorie": col_categorie,
            "symboles":      symboles or {},
            "couleurs":      couleurs or {},
            "couleur":       couleur,
            "symbole":       symbole,
            "taille":        taille,
            "alpha":         alpha,
            "offset":        offset,
            "zorder":        zorder,
        })
        return self

    def ajouter_annotation(
        self,
        cible,
        texte: str,
        *,
        style: str = "callout",
        couleur_fond: str = "#FFFDE7",
        couleur_texte: str = "#1A1A1A",
        couleur_fleche: str = "#333333",
        taille_texte: float = 8,
        offset: tuple = (30, 30),
        zorder: int = 6,
    ) -> "CarteCameroun":
        """
        Ajoute une annotation textuelle sur la carte.

        cible  : nom de zone (str) → centroïde automatique
                 OU (lat, lon) tuple → coordonnées explicites

        style  :
          "callout"   — boîte de texte avec flèche pointant vers la cible
          "highlight" — boîte de texte directement sur la cible, sans flèche
          "valeur"    — texte simple en gras, sans boîte

        offset : décalage du label par rapport à la cible, en kilomètres
                 (ex: (30, 30) = 30 km à droite et 30 km au-dessus).
                 Ignoré pour le style "highlight".

        Retourne self pour permettre le chaînage.
        """
        self._couches.append({
            "type":          "annotation",
            "cible":         cible,
            "texte":         texte,
            "style_ann":     style,
            "couleur_fond":  couleur_fond,
            "couleur_texte": couleur_texte,
            "couleur_fleche":couleur_fleche,
            "taille_texte":  taille_texte,
            "offset":        offset,
            "zorder":        zorder,
        })
        return self

    def ajouter_symboles_proportionnels(
        self,
        col_valeur: str = "valeur",
        couleur: str = "#E74C3C",
        alpha: float = 0.55,
        echelle: float = 1.0,
        afficher_valeur: bool = True,
        zorder: int = 7,
    ) -> "CarteCameroun":
        """
        Superpose des cercles proportionnels sur la carte choroplèthe.
        L'aire de chaque cercle est proportionnelle à la valeur de la zone,
        ce qui permet d'afficher une 2ᵉ variable en plus de la couleur.

        col_valeur      : colonne numérique du GeoDataFrame joint
        couleur         : couleur des cercles (hex ou nom)
        alpha           : transparence (0=invisible, 1=opaque)
        echelle         : facteur de taille global (1.0 = auto, 0.5 = moitié)
        afficher_valeur : écrire la valeur formatée au centre du cercle
        zorder          : ordre de superposition (> 3 pour être au-dessus du fond)

        Retourne self pour permettre le chaînage.

        Exemple :
            carte.charger_geo().charger_metrique(df)
            carte.ajouter_symboles_proportionnels(col_valeur="population")
            carte.visualiser(titre="Taux de pauvreté + population")
        """
        self._couches.append({
            "type":             "symboles_prop",
            "col_valeur":       col_valeur,
            "couleur":          couleur,
            "alpha":            alpha,
            "echelle":          echelle,
            "afficher_valeur":  afficher_valeur,
            "zorder":           zorder,
        })
        return self

    def reinitialiser_couches(self) -> "CarteCameroun":
        """Efface toutes les couches superposées accumulées."""
        self._couches = []
        return self

    # ── RENDU ─────────────────────────────────────────────────────────────────

    def visualiser(
        self,
        titre: str = "Carte du Cameroun",
        unite: str = "",
        methode: str = "quantile",
        n_classes: int = 5,
        schema_couleur: str = "YlOrRd",
        afficher_labels: bool = True,
        afficher_contexte: bool = True,
        sous_titre: str = None,
        source: str = None,
        message_cle: str = None,
        sortie: str = None,
        **kwargs
    ) -> plt.Figure:
        """
        Génère la carte statique (PNG/SVG).

        afficher_contexte : si True et qu'un focus/drill-down a été appliqué,
                            dessine le reste du Cameroun en gris clair en arrière-plan
                            pour donner le contexte géographique.
        """
        if self.gdf_joint is None:
            raise CarteCamerounError(
                "Les données ne sont pas chargées. "
                "Appelez charger_metrique() avant cette méthode."
            )

        gdf_contexte = getattr(self, "_gdf_contexte", None)
        zone_focus   = getattr(self, "_zone_focus", None)

        if zone_focus and afficher_contexte and gdf_contexte is not None:
            return _carte_avec_contexte(
                gdf_focus=self.gdf_joint,
                gdf_contexte=gdf_contexte,
                titre=titre,
                col_nom=self._col_nom,
                schema_couleur=schema_couleur,
                methode_classification=methode,
                n_classes=n_classes,
                unite=unite,
                afficher_labels=afficher_labels,
                zone_focus=zone_focus,
                sortie=sortie,
                style=self.style,
                sous_titre=sous_titre,
                source=source,
                message_cle=message_cle,
                couches=self._couches or None,
            )

        return carte_statique(
            self.gdf_joint,
            titre=titre,
            col_nom=self._col_nom,
            schema_couleur=schema_couleur,
            methode_classification=methode,
            n_classes=n_classes,
            unite=unite,
            afficher_labels=afficher_labels,
            sortie=sortie,
            style=self.style,
            sous_titre=sous_titre,
            source=source,
            message_cle=message_cle,
            couches=self._couches or None,
            **kwargs
        )

    def visualiser_multiples(
        self,
        col_valeurs: list,
        titres: list = None,
        unite: str = "",
        methode: str = "quantile",
        n_classes: int = 5,
        schema_couleur: str = "YlOrRd",
        style: "Style" = None,
        source: str = None,
        sortie: str = None,
        figsize_par_carte: tuple = (6, 7),
    ) -> plt.Figure:
        """
        Affiche plusieurs indicateurs côte à côte (petits multiples).
        Chaque colonne de col_valeurs produit une mini-carte avec la même échelle visuelle.

        col_valeurs      : liste de colonnes numériques du GeoDataFrame joint
                           ex: ["taux_scolarisation", "taux_pauvrete", "acces_eau"]
        titres           : titres des sous-cartes (même longueur que col_valeurs)
        figsize_par_carte: taille individuelle de chaque mini-carte en pouces

        Exemple :
            carte.charger_geo().charger_metrique(df_large)
            carte.visualiser_multiples(
                col_valeurs=["scolarisation", "pauvrete", "sante"],
                titres=["Scolarisation (%)", "Pauvreté (%)", "Santé"],
                sortie="comparaison.png",
            )
        """
        if self.gdf_joint is None:
            raise CarteCamerounError(
                "Les données ne sont pas chargées. "
                "Appelez charger_metrique() avant cette méthode."
            )

        n = len(col_valeurs)
        if n == 0:
            raise ValueError("col_valeurs ne peut pas être vide.")
        if titres is None:
            titres = col_valeurs
        if len(titres) != n:
            raise ValueError("titres doit avoir la même longueur que col_valeurs.")

        s = style or self.style or Style()
        _appliquer_typographie(s)

        w, h = figsize_par_carte
        fig, axes = plt.subplots(1, n, figsize=(w * n, h))
        if n == 1:
            axes = [axes]
        fig.patch.set_facecolor(s.fond_figure)

        gdf_proj = self.gdf_joint.to_crs(CRS_LOCAL)

        for ax, col, titre in zip(axes, col_valeurs, titres):
            ax.set_facecolor(s.fond_carte)
            ax.axis("off")

            if col not in gdf_proj.columns:
                ax.set_title(f"{titre}\n(colonne absente)", fontsize=s.taille_titre - 2,
                             color="#CC0000")
                continue

            gdf_data = gdf_proj[gdf_proj[col].notna()].copy()
            gdf_manq = gdf_proj[gdf_proj[col].isna()]

            if not gdf_manq.empty:
                gdf_manq.plot(ax=ax, color=s.manquant_couleur,
                              edgecolor=s.contours_zones_couleur,
                              linewidth=s.contours_zones_epaisseur,
                              hatch=s.manquant_hatch)

            if not gdf_data.empty:
                bornes, etiquettes = classifier_valeurs(gdf_data[col], methode, n_classes)
                cmap = _resoudre_palette(s, schema_couleur, len(bornes) - 1)
                norm = BoundaryNorm(bornes, cmap.N)
                gdf_data.plot(ax=ax, column=col, cmap=cmap, norm=norm,
                              edgecolor=s.contours_zones_couleur,
                              linewidth=s.contours_zones_epaisseur,
                              legend=False)
                # Mini-légende sous la carte
                patches = [
                    mpatches.Patch(
                        facecolor=cmap(i / max(len(etiquettes) - 1, 1)),
                        edgecolor="#AAAAAA", linewidth=0.3,
                        label=f"{e}{(' ' + unite) if unite else ''}",
                    )
                    for i, e in enumerate(etiquettes)
                ]
                ax.legend(handles=patches, loc="lower left", bbox_to_anchor=(0, 0),
                          ncol=min(len(patches), 3), fontsize=6.5,
                          frameon=True, framealpha=0.9, edgecolor="#DDDDDD",
                          fancybox=False, handlelength=0.8, handleheight=0.7,
                          borderpad=0.4, labelspacing=0.2, columnspacing=0.6)

            ax.set_title(titre, fontsize=s.taille_titre - 2, fontweight="bold",
                         pad=8, color=s.couleur_titre,
                         fontfamily=s.police or "sans-serif")
            if s.bordure_carte:
                _ajouter_bordure(ax)

        # Source commune en bas de la figure
        texte_source = source if source is not None else "Source : GADM v4.1"
        fig.text(0.5, 0.01, texte_source, ha="center",
                 fontsize=s.taille_source, color="#999999")

        plt.tight_layout(pad=1.2)

        if sortie:
            fig.savefig(sortie, dpi=s.dpi, bbox_inches="tight",
                        pad_inches=0.15, facecolor=fig.get_facecolor())
            logger.info(f"[ok] Petits multiples sauvegardés : {sortie}")

        return fig

    def visualiser_bivariee(
        self,
        col_x: str,
        col_y: str,
        titre: str = "Carte bivariée",
        label_x: str = None,
        label_y: str = None,
        n_classes: int = 3,
        palette_bv: list = None,
        style: "Style" = None,
        source: str = None,
        message_cle: str = None,
        sortie: str = None,
        figsize: tuple = (13, 14),
    ) -> plt.Figure:
        """
        Carte bivariée : deux variables encodées simultanément par une matrice de couleurs n×n.
        Typiquement utilisée pour montrer la corrélation spatiale entre deux indicateurs.

        col_x, col_y : colonnes numériques du GeoDataFrame joint
        label_x/y    : libellés pour la légende (défaut = nom de colonne)
        n_classes    : dimension de la matrice (2, 3 ou 4 — 3 recommandé)
        palette_bv   : matrice de couleurs n×n sous forme de liste de n² hex
                       (ligne 0 = faible col_x ; colonne 0 = faible col_y)
                       Défaut : bleu (col_x) × orange (col_y)

        Exemple :
            carte.charger_geo().charger_metrique(df)
            carte.visualiser_bivariee(
                col_x="taux_pauvrete",
                col_y="densite_pop",
                titre="Pauvreté × Densité",
                label_x="Pauvreté", label_y="Densité",
                message_cle="Le Littoral cumule forte densité et faible pauvreté",
            )
        """
        if self.gdf_joint is None:
            raise CarteCamerounError(
                "Les données ne sont pas chargées. "
                "Appelez charger_metrique() avant cette méthode."
            )
        for col in (col_x, col_y):
            if col not in self.gdf_joint.columns:
                raise ValueError(
                    f"Colonne '{col}' absente du GeoDataFrame joint. "
                    f"Colonnes disponibles : {list(self.gdf_joint.columns)}"
                )

        s = style or self.style or Style()
        _appliquer_typographie(s)
        lx = label_x or col_x
        ly = label_y or col_y

        # ── Palette bivariée par défaut : bleu × orange ──────────────────────
        # Matrice n×n : ligne i = classe de col_x (0=faible→n-1=élevé)
        #               col  j = classe de col_y (0=faible→n-1=élevé)
        if palette_bv is None:
            if n_classes == 3:
                palette_bv = [
                    "#E8E8E8", "#ACE4E4", "#5AC8C8",  # col_x faible
                    "#DFB0D6", "#A5ADD3", "#5698B9",  # col_x moyen
                    "#BE64AC", "#8C62AA", "#3B4994",  # col_x élevé
                ]
            elif n_classes == 2:
                palette_bv = ["#E8E8E8", "#73AE80", "#6C83B5", "#2A5A5B"]
            else:
                raise ValueError(
                    f"n_classes={n_classes} non supporté avec la palette par défaut. "
                    "Fournissez palette_bv manuellement ou utilisez n_classes=2 ou 3."
                )

        if len(palette_bv) != n_classes ** 2:
            raise ValueError(
                f"palette_bv doit contenir {n_classes**2} couleurs pour n_classes={n_classes}."
            )

        # ── Classification des deux variables ────────────────────────────────
        gdf = self.gdf_joint.copy()
        gdf_proj = gdf.to_crs(CRS_LOCAL)

        def _classe(series, n):
            """Retourne la classe quantile (0 à n-1) pour chaque valeur."""
            quantiles = np.linspace(0, 1, n + 1)
            bornes = series.quantile(quantiles).unique()
            n_eff = len(bornes) - 1
            cls = pd.cut(series, bins=bornes, labels=False, include_lowest=True)
            return cls.clip(0, n_eff - 1).fillna(-1).astype(int)

        valx = gdf_proj[col_x]
        valy = gdf_proj[col_y]
        gdf_proj["_cls_x"] = _classe(valx.dropna().reindex(valx.index, fill_value=np.nan), n_classes)
        gdf_proj["_cls_y"] = _classe(valy.dropna().reindex(valy.index, fill_value=np.nan), n_classes)

        def _couleur_bv(row):
            cx, cy = row["_cls_x"], row["_cls_y"]
            if cx < 0 or cy < 0:
                return s.manquant_couleur
            return palette_bv[cx * n_classes + cy]

        gdf_proj["_couleur_bv"] = gdf_proj.apply(_couleur_bv, axis=1)

        # ── Rendu ─────────────────────────────────────────────────────────────
        fig, ax = plt.subplots(1, 1, figsize=figsize)
        fig.patch.set_facecolor(s.fond_figure)
        ax.set_facecolor(s.fond_carte)

        gdf_proj.plot(
            ax=ax, color=gdf_proj["_couleur_bv"],
            edgecolor=s.contours_zones_couleur,
            linewidth=s.contours_zones_epaisseur,
        )

        # ── Légende bivariée (carré n×n en bas à gauche) ─────────────────────
        leg_x0, leg_y0 = 0.02, 0.02   # coin bas-gauche en coordonnées axes
        cell = 0.045                   # taille d'une cellule
        for i in range(n_classes):
            for j in range(n_classes):
                couleur = palette_bv[i * n_classes + j]
                rect = plt.Rectangle(
                    (leg_x0 + j * cell, leg_y0 + i * cell), cell, cell,
                    transform=ax.transAxes, clip_on=False,
                    facecolor=couleur, edgecolor="white", linewidth=0.5, zorder=20,
                )
                ax.add_patch(rect)

        # Flèches et labels des axes de la légende
        arr_kw = dict(transform=ax.transAxes, clip_on=False,
                      arrowprops=dict(arrowstyle="->", color="#333333", lw=1.0),
                      fontsize=7.5, color="#333333", zorder=21)
        ax.annotate(
            f"→ {lx}", xytext=(leg_x0, leg_y0 - 0.025),
            xy=(leg_x0 + n_classes * cell, leg_y0 - 0.025),
            ha="left", va="center", **arr_kw,
        )
        ax.annotate(
            f"↑ {ly}", xytext=(leg_x0 - 0.025, leg_y0),
            xy=(leg_x0 - 0.025, leg_y0 + n_classes * cell),
            ha="center", va="bottom", rotation=90, **arr_kw,
        )

        # ── Titre ─────────────────────────────────────────────────────────────
        titre_pad = 52 if message_cle else 14
        ax.set_title(
            titre, fontsize=s.taille_titre, fontweight="bold", pad=titre_pad,
            color=s.couleur_titre, fontfamily=s.police or "sans-serif",
        )
        if message_cle:
            _ajouter_message_cle(ax, message_cle, s)

        ax.axis("off")
        if s.bordure_carte:
            _ajouter_bordure(ax)

        texte_source = source if source is not None else "Source : GADM v4.1"
        ax.text(0.99, 0.01, texte_source,
                transform=ax.transAxes, fontsize=s.taille_source,
                color="#999999", ha="right")

        plt.tight_layout(pad=1.2)

        if sortie:
            fig.savefig(sortie, dpi=s.dpi, bbox_inches="tight",
                        pad_inches=0.15, facecolor=fig.get_facecolor())
            logger.info(f"[ok] Carte bivariée sauvegardée : {sortie}")

        return fig

    def visualiser_interactif(
        self,
        titre: str = "Cameroun",
        moteur: str = "folium",
        unite: str = "",
        methode: str = "quantile",
        n_classes: int = 5,
        schema_couleur: str = "YlOrRd",
        sortie: str = None,
    ):
        """
        Génère la carte interactive.
        moteur : 'folium' (HTML) ou 'plotly' (Dash/Streamlit)
        """
        if self.gdf_joint is None:
            raise CarteCamerounError(
                "Les données ne sont pas chargées. "
                "Appelez charger_metrique() avant cette méthode."
            )

        if moteur == "folium":
            return carte_interactive_folium(
                self.gdf_joint,
                titre=titre,
                col_nom=self._col_nom,
                schema_couleur=schema_couleur,
                methode_classification=methode,
                n_classes=n_classes,
                unite=unite,
                sortie=sortie or "carte_cameroun.html"
            )
        elif moteur == "plotly":
            return carte_plotly(
                self.gdf_joint,
                titre=titre,
                col_nom=self._col_nom,
                schema_couleur=schema_couleur,
                unite=unite,
                sortie=sortie or "carte_cameroun.html"
            )
        else:
            raise ValueError(f"Moteur inconnu : {moteur}. Choisir 'folium' ou 'plotly'")


# ─────────────────────────────────────────────────────────────────────────────
# 9b. RENDU AVEC CONTEXTE GÉOGRAPHIQUE (fond grisé + zone en couleur)
# ─────────────────────────────────────────────────────────────────────────────

def _carte_avec_contexte(
    gdf_focus: gpd.GeoDataFrame,
    gdf_contexte: gpd.GeoDataFrame,
    titre: str,
    col_nom: str,
    schema_couleur: str,
    methode_classification: str,
    n_classes: int,
    unite: str,
    afficher_labels: bool,
    zone_focus: str,
    sortie: str = None,
    figsize: tuple = (12, 14),
    style: "Style" = None,
    sous_titre: str = None,
    source: str = None,
    message_cle: str = None,
    couches: list = None,
) -> plt.Figure:
    """
    Dessine une carte en deux couches :
    - Couche 1 (fond) : toutes les zones du Cameroun en gris très clair
    - Couche 2 (premier plan) : zone(s) focalisée(s) avec la métrique en couleur

    Le fond donne le contexte géographique, la zone d'intérêt ressort clairement.
    """
    s = style or Style()
    _appliquer_typographie(s)

    gdf_contexte_proj = gdf_contexte.to_crs(CRS_LOCAL)
    gdf_focus_proj    = gdf_focus.to_crs(CRS_LOCAL)

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    fig.patch.set_facecolor(s.fond_figure)
    ax.set_facecolor(s.fond_carte)

    # ── Fond : tout le Cameroun en gris pâle ─────────────────────────────────
    gdf_contexte_proj.plot(
        ax=ax, color="#E8E8E4", edgecolor="#BBBBBB", linewidth=0.4, zorder=1,
    )

    # ── Focus : zones colorées par métrique ──────────────────────────────────
    gdf_data = gdf_focus_proj[~gdf_focus_proj["manquant"]]
    gdf_manq = gdf_focus_proj[gdf_focus_proj["manquant"]]

    if not gdf_manq.empty:
        gdf_manq.plot(
            ax=ax, color=s.manquant_couleur,
            edgecolor=s.contours_zones_couleur,
            linewidth=s.contours_zones_epaisseur,
            hatch=s.manquant_hatch, zorder=2,
        )

    if not gdf_data.empty:
        valeurs = gdf_data["valeur"]
        bornes, etiquettes = classifier_valeurs(valeurs, methode_classification, n_classes)
        cmap = _resoudre_palette(s, schema_couleur, len(bornes) - 1)
        norm = BoundaryNorm(bornes, cmap.N)

        gdf_data.plot(
            ax=ax, column="valeur", cmap=cmap, norm=norm,
            edgecolor=s.contours_zones_couleur,
            linewidth=s.contours_zones_epaisseur,
            zorder=3, legend=False,
        )

        patches = [
            mpatches.Patch(
                facecolor=cmap(i / max(len(etiquettes) - 1, 1)),
                edgecolor="#AAAAAA", linewidth=0.3,
                label=f"{e}{(' ' + unite) if unite else ''}",
            )
            for i, e in enumerate(etiquettes)
        ]
        if not gdf_manq.empty:
            patches.append(mpatches.Patch(
                facecolor=s.manquant_couleur, hatch=s.manquant_hatch,
                edgecolor="#AAAAAA", linewidth=0.3, label="N/D",
            ))
        _construire_legende(ax, patches, s)

    # ── Contour de surbrillance ───────────────────────────────────────────────
    gdf_focus_proj.boundary.plot(
        ax=ax, color=s.contours_pays_couleur, linewidth=1.2, zorder=4,
    )

    # ── Labels ───────────────────────────────────────────────────────────────
    if afficher_labels and col_nom:
        for _, row in gdf_focus_proj.iterrows():
            if row.geometry is None:
                continue
            centroid = row.geometry.centroid
            nom = str(row.get(col_nom, ""))
            if nom and nom != "nan":
                ax.annotate(
                    nom, xy=(centroid.x, centroid.y),
                    ha="center", va="center",
                    fontsize=s.taille_labels + 0.5,
                    color="#111111", fontweight="bold",
                    fontfamily=s.police or "sans-serif",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white",
                              alpha=0.7, ec="none"),
                    zorder=5,
                )

    # ── Couches superposées ───────────────────────────────────────────────────
    if couches:
        _rendre_couches(ax, couches, gdf_focus_proj, col_nom, CRS_LOCAL)

    # ── Titre ────────────────────────────────────────────────────────────────
    titre_pad = 52 if message_cle else 14
    ax.set_title(
        titre, fontsize=s.taille_titre, fontweight="bold", pad=titre_pad,
        color=s.couleur_titre, fontfamily=s.police or "sans-serif",
    )
    if sous_titre:
        y_st = 1.075 if message_cle else 1.005
        ax.text(
            0.5, y_st, sous_titre,
            transform=ax.transAxes,
            fontsize=s.taille_sous_titre, color="#555555",
            ha="center", va="bottom",
            fontfamily=s.police or "sans-serif",
        )
    if message_cle:
        _ajouter_message_cle(ax, message_cle, s)

    # ── Zoom + cadrage sur la zone focalisée ─────────────────────────────────
    xmin, ymin, xmax, ymax = gdf_focus_proj.total_bounds
    mx, my = (xmax - xmin) * 0.10, (ymax - ymin) * 0.10
    ax.set_xlim(xmin - mx, xmax + mx)
    ax.set_ylim(ymin - my, ymax + my)

    ax.axis("off")
    if s.bordure_carte:
        _ajouter_bordure(ax)
    _ajouter_echelle(ax, gdf_focus_proj)

    texte_source = source if source is not None else f"Focus : {zone_focus} | Source : GADM v4.1"
    ax.text(0.99, 0.01, texte_source,
            transform=ax.transAxes, fontsize=s.taille_source,
            color="#999999", ha="right")

    plt.tight_layout(pad=1.2)

    if sortie:
        fig.savefig(sortie, dpi=s.dpi, bbox_inches="tight",
                    pad_inches=0.15, facecolor=fig.get_facecolor())
        logger.info(f"[ok] Carte sauvegardée : {sortie}")

    return fig


# ─────────────────────────────────────────────────────────────────────────────
# 10. DÉMONSTRATION — données simulées
# ─────────────────────────────────────────────────────────────────────────────

def demo_avec_donnees_simulees():
    """
    Démo complète avec données simulées si GADM non disponible.
    Crée un GeoDataFrame factice pour tester le pipeline.
    """
    from shapely.geometry import Polygon
    import random, math
    
    print("\n=== DÉMO avec données simulées ===\n")
    
    # Polygones simplifiés pour les 10 régions
    regions_demo = {
        "Adamaoua":    (12.5, 6.5, 14.5, 8.5),
        "Centre":      (11.0, 3.0, 13.5, 5.5),
        "Est":         (13.5, 2.0, 16.2, 5.5),
        "Extrême-Nord":(14.0, 9.5, 15.5, 12.5),
        "Littoral":    (9.0,  3.5, 11.0, 5.5),
        "Nord":        (13.5, 7.0, 15.0, 10.0),
        "Nord-Ouest":  (9.0,  5.5, 11.5, 7.5),
        "Ouest":       (9.5,  4.5, 11.5, 6.5),
        "Sud":         (10.0, 1.5, 13.5, 3.5),
        "Sud-Ouest":   (8.5,  3.0, 10.0, 5.5),
    }
    
    rows = []
    for nom, (x0, y0, x1, y1) in regions_demo.items():
        geom = Polygon([(x0,y0),(x1,y0),(x1,y1),(x0,y1)])
        rows.append({"NAME_1": nom, "geometry": geom})
    
    gdf = gpd.GeoDataFrame(rows, crs=CRS_GLOBAL)
    
    # Données métriques simulées (avec intentionnellement un nom mal orthographié)
    np.random.seed(42)
    df_metrique = pd.DataFrame({
        "region": ["Adamaoua", "centre", "Est", "extreme nord",
                   "Littoral", "nord", "Nord-Ouest", "Ouest",
                   "Sud", "sud ouest"],  # orthographes variées → test fuzzy
        "taux_acces_eau": np.random.uniform(20, 95, 10).round(1),
    })
    
    print("Données métriques brutes (avec noms volontairement mal orthographiés) :")
    print(df_metrique.to_string(index=False))
    print()
    
    # Pipeline complet
    gdf_joint = joindre_metrique(
        gdf, df_metrique,
        col_geo="NAME_1",
        col_metrique="region",
        col_valeur="taux_acces_eau",
        fuzzy=True
    )
    
    # Carte statique
    fig = carte_statique(
        gdf_joint,
        titre="Accès à l'eau potable par région — Cameroun (démo)",
        col_nom="NAME_1",
        schema_couleur="YlGn",
        methode_classification="quantile",
        n_classes=5,
        unite="%",
        afficher_labels=True,
        sortie="carte_demo.png"
    )
    
    plt.show()
    print("\n[démo terminée] Carte sauvegardée : carte_demo.png")
    return gdf_joint


if __name__ == "__main__":
    demo_avec_donnees_simulees()
