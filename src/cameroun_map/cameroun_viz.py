"""
Visualisation cartographique du Cameroun — solution modulaire
Supporte : régions, départements, arrondissements, quartiers (OSM)
"""

import geopandas as gpd
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib import cm
import folium
from folium.plugins import FloatImage
import plotly.express as px
import requests, zipfile, io, os, re
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

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

def charger_gadm(niveau: int = 1, forcer_telechargement: bool = False) -> gpd.GeoDataFrame:
    """
    Charge les limites administratives depuis GADM (gadm.org).
    niveau : 0=pays, 1=régions, 2=départements, 3=arrondissements
    
    Stratégie de cache : le GeoJSON est sauvegardé localement.
    """
    cache_path = DATA_DIR / f"cameroun_gadm_niveau{niveau}.geojson"

    if cache_path.exists() and not forcer_telechargement:
        print(f"[cache] Chargement GADM niveau {niveau} depuis {cache_path}")
        return gpd.read_file(cache_path)

    # URL officielle GADM v4.1
    url = f"https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_CMR_{niveau}.json"
    print(f"[download] Téléchargement GADM niveau {niveau} : {url}")
    
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        gdf = gpd.read_file(io.BytesIO(resp.content))
        gdf.to_file(cache_path, driver="GeoJSON")
        print(f"[ok] {len(gdf)} entités chargées, sauvegardées dans {cache_path}")
        return gdf
    except Exception as e:
        print(f"[erreur] Téléchargement échoué : {e}")
        print("  → Téléchargez manuellement sur https://gadm.org/download_country.html (Cameroon)")
        print(f"  → Placez le fichier GeoJSON dans {cache_path}")
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
    print(f"[osm] Requête Overpass pour {ville}...")
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
            print(f"[osm] Aucun quartier trouvé pour {ville}. Vérifiez la requête Overpass.")
            return gpd.GeoDataFrame()
        
        gdf = gpd.GeoDataFrame(rows, crs=CRS_GLOBAL)
        cache_path = DATA_DIR / f"osm_{ville.lower()}_quartiers.geojson"
        gdf.to_file(cache_path, driver="GeoJSON")
        print(f"[ok] {len(gdf)} quartiers OSM chargés pour {ville}")
        return gdf
    except Exception as e:
        print(f"[erreur] OSM Overpass : {e}")
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
        print(f"[join] {n_manquant} zone(s) sans données : {zones}")

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 5. SCHÉMAS DE COULEUR & CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

SCHEMAS_COULEUR = {
    "sequentiel":   "YlOrRd",     # valeurs de faible à élevée
    "divergent":    "RdYlGn",     # valeurs autour d'une médiane
    "qualitatif":   "Set2",       # catégories sans ordre
    "population":   "PuBuGn",
    "pauvrete":     "OrRd",
    "sante":        "YlGn",
    "education":    "Blues",
}

METHODES_CLASSIFICATION = ["quantile", "jenks", "égale", "std"]


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
    
    if methode == "quantile":
        bornes = valeurs.quantile(np.linspace(0, 1, n_classes + 1)).unique()
    elif methode == "jenks":
        try:
            import mapclassify
            jnb = mapclassify.NaturalBreaks(valeurs, k=n_classes)
            bornes = np.concatenate([[valeurs.min()], jnb.bins])
        except ImportError:
            print("[warn] mapclassify non installé → bascule sur quantile")
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
        f"{bornes[i]:.1f} – {bornes[i+1]:.1f}"
        for i in range(len(bornes) - 1)
    ]
    return bornes, etiquettes


# ─────────────────────────────────────────────────────────────────────────────
# 6. CARTE STATIQUE — Matplotlib / GeoPandas
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
    figsize: tuple = (12, 14)
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
    # Reprojection en UTM pour une meilleure représentation locale
    gdf_proj = gdf.to_crs(CRS_LOCAL)
    
    fig, ax = plt.subplots(1, 1, figsize=figsize)
    fig.patch.set_facecolor("#F8F8F0")
    ax.set_facecolor("#D4E8F4")  # fond mer/eau
    
    # ── Zones sans données : hachurage gris ──────────────────────────────────
    gdf_manquant = gdf_proj[gdf_proj["manquant"]]
    if not gdf_manquant.empty:
        gdf_manquant.plot(
            ax=ax, color="#D0D0D0", edgecolor="white",
            linewidth=0.6, hatch="///", label="Données manquantes"
        )
    
    # ── Classification & palette ─────────────────────────────────────────────
    gdf_data = gdf_proj[~gdf_proj["manquant"]].copy()
    
    if not gdf_data.empty:
        valeurs = gdf_data[col_valeur]
        bornes, etiquettes = classifier_valeurs(valeurs, methode_classification, n_classes)
        
        cmap     = plt.get_cmap(schema_couleur, len(bornes) - 1)
        norm     = BoundaryNorm(bornes, cmap.N)
        
        gdf_data.plot(
            ax=ax,
            column=col_valeur,
            cmap=cmap,
            norm=norm,
            edgecolor="white",
            linewidth=0.6,
            legend=False,
            missing_kwds={"color": "#D0D0D0", "hatch": "///"}
        )
        
        # ── Légende manuelle ─────────────────────────────────────────────────
        patches = []
        for i, etiq in enumerate(etiquettes):
            couleur = cmap(i / max(len(etiquettes) - 1, 1))
            label   = f"{etiq} {unite}".strip()
            patches.append(mpatches.Patch(facecolor=couleur, edgecolor="grey",
                                          linewidth=0.4, label=label))
        if not gdf_manquant.empty:
            patches.append(mpatches.Patch(
                facecolor="#D0D0D0", hatch="///", edgecolor="grey",
                linewidth=0.4, label="Données manquantes"
            ))
        
        leg = ax.legend(
            handles=patches, loc="lower left",
            title=f"Légende ({methode_classification})",
            title_fontsize=9, fontsize=8,
            framealpha=0.95, edgecolor="#CCCCCC",
            fancybox=False
        )
        leg.get_frame().set_linewidth(0.5)
    
    # ── Contour pays ─────────────────────────────────────────────────────────
    if afficher_contour_pays:
        gdf_proj.boundary.plot(ax=ax, color="#333333", linewidth=0.3, alpha=0.5)
    
    # ── Labels des zones ──────────────────────────────────────────────────────
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
                    fontsize=6.5, color="#222222",
                    fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", alpha=0.6, ec="none")
                )
    
    # ── Esthétique ────────────────────────────────────────────────────────────
    ax.set_title(titre, fontsize=16, fontweight="bold", pad=18, color="#1A1A1A")
    ax.axis("off")
    
    # Rose des vents
    ax.annotate("N", xy=(0.97, 0.97), xycoords="axes fraction",
                fontsize=14, ha="center", va="center", fontweight="bold")
    ax.annotate("↑", xy=(0.97, 0.94), xycoords="axes fraction",
                fontsize=20, ha="center", va="center")
    
    # Échelle (approximative)
    _ajouter_echelle(ax, gdf_proj)
    
    # Source
    ax.text(0.01, 0.01, "Source : GADM v4.1 | Visualisation Python",
            transform=ax.transAxes, fontsize=7, color="#888888")
    
    plt.tight_layout()
    
    if sortie:
        fig.savefig(sortie, dpi=180, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        print(f"[ok] Carte sauvegardée : {sortie}")
    
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
        print(f"[ok] Carte interactive sauvegardée : {sortie}")
    
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
        print(f"[ok] Carte Plotly sauvegardée : {sortie}")
    
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
    
    def __init__(self, niveau: str = "regions"):
        """
        niveau : 'pays', 'regions', 'departements', 'arrondissements', 'osm_quartiers'
        """
        if niveau not in list(GADM_LEVELS.keys()) + ["osm_quartiers"]:
            raise ValueError(f"Niveau inconnu : {niveau}. Choisir parmi {list(GADM_LEVELS)}")
        
        self.niveau    = niveau
        self.gdf       = None    # GeoDataFrame géographique
        self.gdf_joint = None    # GeoDataFrame après jointure métrique
        self._col_nom  = None    # colonne de nom identifiée
    
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
        
        print(f"[ok] {len(self.gdf)} zones chargées pour le niveau '{self.niveau}'")
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
        assert self.gdf is not None, "Appelez d'abord charger_geo()"
        
        if diagnostic:
            diagnostiquer_dataset(df)
        
        if auto_adapter:
            df_std = adapter_dataset(df, col_zone=col_nom, col_valeur=col_valeur)
            col_nom_utilise   = "zone"
            col_valeur_utilise = "valeur"
        else:
            assert col_nom and col_valeur, (
                "Avec auto_adapter=False, col_nom et col_valeur sont obligatoires."
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
        print(f"[ok] Jointure réussie — {(~self.gdf_joint['manquant']).sum()}/{len(self.gdf_joint)} zones avec données")
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
        assert self.gdf_joint is not None, "Appelez d'abord charger_metrique()"

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
        assert self.gdf_joint is not None, "Appelez d'abord charger_metrique()"

        col = col_parent or self._col_nom
        masque = self.gdf_joint[col].str.lower() == zone.lower()

        if masque.sum() == 0:
            # Tentative de matching approximatif
            valeurs_dispo = self.gdf_joint[col].dropna().unique().tolist()
            try:
                from thefuzz import process as fp
                match, score = fp.extractOne(zone, valeurs_dispo)
                if score >= 70:
                    print(f"[focus] '{zone}' non trouvé → correspondance : '{match}' ({score}%)")
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

        print(f"[focus] Zone isolée : '{zone}' — {masque.sum()} entité(s)")
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
        print(f"[drill_down] '{zone}' → {len(gdf_zone)} {niveau_interieur}")

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

    def visualiser(
        self,
        titre: str = "Carte du Cameroun",
        unite: str = "",
        methode: str = "quantile",
        n_classes: int = 5,
        schema_couleur: str = "YlOrRd",
        afficher_labels: bool = True,
        afficher_contexte: bool = True,
        sortie: str = None,
        **kwargs
    ) -> plt.Figure:
        """
        Génère la carte statique (PNG/SVG).

        afficher_contexte : si True et qu'un focus/drill-down a été appliqué,
                            dessine le reste du Cameroun en gris clair en arrière-plan
                            pour donner le contexte géographique.
        """
        assert self.gdf_joint is not None, "Appelez d'abord charger_metrique()"

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
            **kwargs
        )

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
        assert self.gdf_joint is not None, "Appelez d'abord charger_metrique()"

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
) -> plt.Figure:
    """
    Dessine une carte en deux couches :
    - Couche 1 (fond) : toutes les zones du Cameroun en gris très clair
    - Couche 2 (premier plan) : zone(s) focalisée(s) avec la métrique en couleur

    Le fond donne le contexte géographique, la zone d'intérêt ressort clairement.
    """
    gdf_contexte_proj = gdf_contexte.to_crs(CRS_LOCAL)
    gdf_focus_proj    = gdf_focus.to_crs(CRS_LOCAL)

    fig, ax = plt.subplots(1, 1, figsize=figsize)
    fig.patch.set_facecolor("#F8F8F0")
    ax.set_facecolor("#D4E8F4")

    # ── Couche fond : tout le Cameroun, gris pâle ─────────────────────────────
    gdf_contexte_proj.plot(
        ax=ax,
        color="#E8E8E4",
        edgecolor="#BBBBBB",
        linewidth=0.4,
        zorder=1,
    )

    # ── Couche focus : zones colorées par métrique ───────────────────────────
    gdf_data = gdf_focus_proj[~gdf_focus_proj["manquant"]]
    gdf_manq = gdf_focus_proj[gdf_focus_proj["manquant"]]

    if not gdf_manq.empty:
        gdf_manq.plot(
            ax=ax, color="#D0D0D0", edgecolor="white",
            linewidth=0.6, hatch="///", zorder=2
        )

    if not gdf_data.empty:
        valeurs = gdf_data["valeur"]
        bornes, etiquettes = classifier_valeurs(valeurs, methode_classification, n_classes)
        cmap = plt.get_cmap(schema_couleur, len(bornes) - 1)
        norm = BoundaryNorm(bornes, cmap.N)

        gdf_data.plot(
            ax=ax, column="valeur",
            cmap=cmap, norm=norm,
            edgecolor="white", linewidth=0.8,
            zorder=3, legend=False,
        )

        # Légende
        patches = [
            mpatches.Patch(
                facecolor=cmap(i / max(len(etiquettes) - 1, 1)),
                edgecolor="grey", linewidth=0.4,
                label=f"{e} {unite}".strip()
            )
            for i, e in enumerate(etiquettes)
        ]
        if not gdf_manq.empty:
            patches.append(mpatches.Patch(
                facecolor="#D0D0D0", hatch="///", edgecolor="grey",
                linewidth=0.4, label="Données manquantes"
            ))
        leg = ax.legend(handles=patches, loc="lower left",
                        title=f"Légende ({methode_classification})",
                        title_fontsize=9, fontsize=8,
                        framealpha=0.95, edgecolor="#CCCCCC", fancybox=False)
        leg.get_frame().set_linewidth(0.5)

    # ── Contour de surbrillance autour de la zone focalisée ──────────────────
    gdf_focus_proj.boundary.plot(ax=ax, color="#222222", linewidth=1.2, zorder=4)

    # ── Labels ────────────────────────────────────────────────────────────────
    if afficher_labels and col_nom:
        for _, row in gdf_focus_proj.iterrows():
            if row.geometry is None:
                continue
            centroid = row.geometry.centroid
            nom = str(row.get(col_nom, ""))
            if nom and nom != "nan":
                ax.annotate(
                    nom, xy=(centroid.x, centroid.y),
                    ha="center", va="center", fontsize=7,
                    color="#111111", fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", alpha=0.7, ec="none"),
                    zorder=5,
                )

    # ── Esthétique ────────────────────────────────────────────────────────────
    ax.set_title(titre, fontsize=16, fontweight="bold", pad=18, color="#1A1A1A")

    # Zoom automatique sur la zone focalisée + marge de 10 %
    xmin, ymin, xmax, ymax = gdf_focus_proj.total_bounds
    marge_x = (xmax - xmin) * 0.10
    marge_y = (ymax - ymin) * 0.10
    ax.set_xlim(xmin - marge_x, xmax + marge_x)
    ax.set_ylim(ymin - marge_y, ymax + marge_y)

    ax.axis("off")
    _ajouter_echelle(ax, gdf_focus_proj)

    # Annotation de localisation
    ax.text(0.99, 0.01, f"Focus : {zone_focus} | Source : GADM v4.1",
            transform=ax.transAxes, fontsize=7,
            color="#888888", ha="right")

    plt.tight_layout()

    if sortie:
        fig.savefig(sortie, dpi=180, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        print(f"[ok] Carte sauvegardée : {sortie}")

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
