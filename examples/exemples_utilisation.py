"""
exemples_utilisation.py
=======================
Exemples concrets d'utilisation de la solution CarteCameroun.
Chaque bloc est indépendant — décommentez ce dont vous avez besoin.
"""

import pandas as pd
import numpy as np
from cameroun_map import CarteCameroun, SCHEMAS_COULEUR


# ─────────────────────────────────────────────────────────────────────────────
# EXEMPLE 1 : Carte par régions (niveau 1) — la plus simple
# ─────────────────────────────────────────────────────────────────────────────

def exemple_regions():
    """
    Visualiser un indicateur (ex: taux de pauvreté) par région.
    CSV attendu : colonnes 'region' et 'taux_pauvrete'
    """
    # Chargement de vos données
    df = pd.read_csv("donnees_regions.csv")
    #   region         | taux_pauvrete
    #   Adamaoua       | 67.2
    #   Centre         | 30.1
    #   ...

    carte = CarteCameroun(niveau="regions")
    carte.charger_geo()
    carte.charger_metrique(df, col_nom="region", col_valeur="taux_pauvrete")
    carte.diagnostiquer()

    # Carte statique haute résolution
    carte.visualiser(
        titre="Taux de pauvreté par région — Cameroun 2024",
        unite="%",
        methode="jenks",          # ruptures naturelles : bon pour données hétérogènes
        n_classes=5,
        schema_couleur="OrRd",    # orange→rouge : intuitif pour pauvreté
        afficher_labels=True,
        sortie="carte_pauvrete_regions.png"
    )

    # Carte interactive HTML (à ouvrir dans un navigateur)
    carte.visualiser_interactif(
        titre="Taux de pauvreté — Cameroun",
        moteur="folium",
        unite="%",
        sortie="carte_pauvrete_regions.html"
    )


# ─────────────────────────────────────────────────────────────────────────────
# EXEMPLE 2 : Carte par départements (niveau 2)
# ─────────────────────────────────────────────────────────────────────────────

def exemple_departements():
    """
    Visualiser un indicateur de santé par département.
    """
    df = pd.read_csv("donnees_departements.csv")
    #   departement     | couverture_vaccination
    #   Mfoundi         | 82.5
    #   Wouri           | 75.3
    #   ...

    carte = CarteCameroun(niveau="departements")
    carte.charger_geo()
    carte.charger_metrique(df, col_nom="departement", col_valeur="couverture_vaccination")

    carte.visualiser(
        titre="Couverture vaccinale par département — Cameroun",
        unite="%",
        methode="quantile",
        schema_couleur="YlGn",
        afficher_labels=False,    # trop de zones, désactiver les labels
        sortie="carte_vaccination_departements.png"
    )


# ─────────────────────────────────────────────────────────────────────────────
# EXEMPLE 3 : Niveau fin — quartiers de Douala via OSM
# ─────────────────────────────────────────────────────────────────────────────

def exemple_quartiers_douala():
    """
    Carte fine de Douala avec données par quartier.
    Nécessite une connexion internet pour l'API Overpass.
    """
    df = pd.read_csv("donnees_quartiers_douala.csv")
    #   quartier        | densite_population
    #   Akwa            | 18500
    #   Bonamoussadi    | 12300
    #   ...

    carte = CarteCameroun(niveau="osm_quartiers")
    carte.charger_geo(ville_osm="Douala")
    carte.charger_metrique(df, col_nom="quartier", col_valeur="densite_population")

    carte.visualiser(
        titre="Densité de population — Douala par quartier",
        unite="hab/km²",
        methode="quantile",
        schema_couleur="YlOrRd",
        afficher_labels=True,
        sortie="carte_douala_densite.png"
    )

    # Pour Plotly (intégrable dans Streamlit/Dash)
    fig = carte.visualiser_interactif(
        titre="Douala — Densité de population",
        moteur="plotly",
        sortie="carte_douala_plotly.html"
    )


# ─────────────────────────────────────────────────────────────────────────────
# EXEMPLE 4 : Carte à plusieurs couches (régions + chef-lieux)
# ─────────────────────────────────────────────────────────────────────────────

def exemple_multicouches():
    """
    Superposer les régions colorées + des points (villes, projets, cliniques...).
    """
    import matplotlib.pyplot as plt
    import geopandas as gpd
    from cameroun_viz import charger_gadm, joindre_metrique, carte_statique

    # Couche 1 : régions avec indicateur
    gdf_regions = charger_gadm(1)
    df_indicateur = pd.read_csv("donnees_regions.csv")
    gdf_joint = joindre_metrique(
        gdf_regions, df_indicateur,
        col_geo="NAME_1", col_metrique="region", col_valeur="valeur"
    )

    fig = carte_statique(
        gdf_joint,
        titre="Projets de santé — Cameroun",
        col_nom="NAME_1",
        schema_couleur="Blues",
        sortie=None   # on ne sauvegarde pas encore
    )
    ax = fig.axes[0]

    # Couche 2 : points (cliniques, projets, etc.)
    df_points = pd.read_csv("localisation_cliniques.csv")
    #   nom       | lon   | lat
    #   Hôpital X | 9.70  | 4.05
    gdf_points = gpd.GeoDataFrame(
        df_points,
        geometry=gpd.points_from_xy(df_points.lon, df_points.lat),
        crs="EPSG:4326"
    ).to_crs("EPSG:32632")

    gdf_points.plot(
        ax=ax, color="red", markersize=30,
        edgecolor="white", linewidth=0.5, zorder=5, alpha=0.9
    )

    # Labels des points
    for _, row in gdf_points.iterrows():
        ax.annotate(
            row["nom"],
            xy=(row.geometry.x, row.geometry.y),
            xytext=(6, 6), textcoords="offset points",
            fontsize=7, color="darkred"
        )

    fig.savefig("carte_multicouches.png", dpi=180, bbox_inches="tight")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────────
# EXEMPLE 5 : Comparaison de méthodes de classification
# ─────────────────────────────────────────────────────────────────────────────

def comparer_classifications():
    """
    Génère 4 cartes côte à côte pour comparer les méthodes de classification.
    Utile pour choisir la meilleure représentation de vos données.
    """
    import matplotlib.pyplot as plt
    from cameroun_viz import charger_gadm, joindre_metrique, carte_statique, METHODES_CLASSIFICATION

    df = pd.read_csv("donnees_regions.csv")
    gdf = charger_gadm(1)
    gdf_joint = joindre_metrique(
        gdf, df, col_geo="NAME_1",
        col_metrique="region", col_valeur="valeur"
    )

    methodes = ["quantile", "jenks", "égale", "std"]
    fig, axes = plt.subplots(2, 2, figsize=(16, 18))
    fig.suptitle("Comparaison des méthodes de classification — même jeu de données",
                 fontsize=14, fontweight="bold")

    for ax, methode in zip(axes.flat, methodes):
        from cameroun_viz import classifier_valeurs
        from matplotlib.colors import BoundaryNorm
        gdf_tmp = gdf_joint.copy().to_crs("EPSG:32632")
        vals = gdf_tmp["valeur"].dropna()
        bornes, etiq = classifier_valeurs(vals, methode, 5)
        cmap = plt.get_cmap("YlOrRd", len(bornes) - 1)
        norm = BoundaryNorm(bornes, cmap.N)
        gdf_tmp[~gdf_tmp["manquant"]].plot(
            ax=ax, column="valeur", cmap=cmap, norm=norm,
            edgecolor="white", linewidth=0.5
        )
        gdf_tmp[gdf_tmp["manquant"]].plot(ax=ax, color="#D0D0D0", edgecolor="white")
        ax.set_title(f"Méthode : {methode}", fontweight="bold")
        ax.axis("off")

    plt.tight_layout()
    fig.savefig("comparaison_classifications.png", dpi=150, bbox_inches="tight")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────────
# POINT D'ENTRÉE
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Décommentez l'exemple souhaité et relancez le script.")
    # exemple_regions()
    # exemple_departements()
    # exemple_quartiers_douala()
    # exemple_multicouches()
    # comparer_classifications()
