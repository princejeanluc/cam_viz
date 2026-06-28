"""
export_powerbi.py
==================
Prépare les deux fichiers nécessaires à une carte Power BI native
(visuel "Shape Map") : un TopoJSON des contours administratifs et
un CSV de données avec une clé de jointure qui correspond exactement
aux noms du TopoJSON.

Pourquoi Power BI a besoin de ça :
Le visuel Shape Map de Power BI ne sait pas dessiner une carte à partir
de code Python. Il accepte uniquement un fichier TopoJSON personnalisé
(Format > Map > "+ Add map") et fait ensuite correspondre, ligne par ligne,
une colonne de votre table de données à la propriété de nom de chaque
forme du TopoJSON. Ce module produit ces deux pièces, déjà alignées.

Usage rapide :

    from export_powerbi import exporter_pour_powerbi

    exporter_pour_powerbi(
        niveau="regions",
        df_metrique=df,
        dossier_sortie="export_powerbi"
    )

Produit :
    export_powerbi/cameroun_regions.topojson
    export_powerbi/donnees_powerbi.csv
    export_powerbi/INSTRUCTIONS_POWERBI.txt
"""

import json
import pandas as pd
import geopandas as gpd
from pathlib import Path

from .cameroun_viz import charger_gadm, GADM_LEVELS, CRS_GLOBAL
from .adapter_dataset import diagnostiquer_dataset, adapter_dataset


# ─────────────────────────────────────────────────────────────────────────────
# 1. CONVERSION GEOJSON → TOPOJSON
# ─────────────────────────────────────────────────────────────────────────────

def geojson_vers_topojson(
    gdf: gpd.GeoDataFrame,
    col_nom: str,
    nom_objet: str = "zones",
    simplification: float = None
) -> dict:
    """
    Convertit un GeoDataFrame en dictionnaire TopoJSON.
    
    col_nom        : colonne à conserver comme identifiant de chaque forme
                     (c'est la clé que Power BI utilisera pour le matching)
    nom_objet      : nom de la collection dans le TopoJSON (ex: "regions")
    simplification : tolérance de simplification des géométries (degrés),
                      utile pour alléger le fichier si les contours sont très
                      détaillés. None = pas de simplification.
    
    Nécessite le package 'topojson' :  pip install topojson
    """
    try:
        import topojson as tp
    except ImportError:
        raise ImportError(
            "Le package 'topojson' est requis pour cette conversion.\n"
            "Installez-le avec : pip install topojson"
        )
    
    # On ne garde que la colonne de nom + géométrie pour un fichier léger
    gdf_export = gdf[[col_nom, "geometry"]].copy()
    gdf_export = gdf_export.rename(columns={col_nom: "nom"})
    
    # Reprojection en WGS84 (obligatoire pour Power BI)
    gdf_export = gdf_export.to_crs(CRS_GLOBAL)
    
    topo = tp.Topology(gdf_export, prequantize=False)
    
    if simplification:
        topo = topo.toposimplify(simplification)
    
    topo_dict = json.loads(topo.to_json())
    
    # Renomme l'objet par défaut ("data") en quelque chose de lisible
    if "data" in topo_dict.get("objects", {}):
        topo_dict["objects"][nom_objet] = topo_dict["objects"].pop("data")
    
    return topo_dict


# ─────────────────────────────────────────────────────────────────────────────
# 2. PRÉPARATION DE LA TABLE DE DONNÉES
# ─────────────────────────────────────────────────────────────────────────────

def preparer_csv_powerbi(
    df: pd.DataFrame,
    col_nom: str = None,
    col_valeur: str = None,
    noms_reference: list = None,
    sortie: str = "donnees_powerbi.csv"
) -> pd.DataFrame:
    """
    Adapte un dataset brut au format attendu par Power BI : une colonne
    'zone' dont les valeurs correspondent EXACTEMENT aux noms du TopoJSON
    (la jointure dans Power BI est une correspondance textuelle stricte,
    contrairement au pipeline Python qui tolère le fuzzy matching).
    
    noms_reference : liste des noms exacts présents dans le TopoJSON.
                      Si fourni, les noms du CSV sont alignés dessus
                      (fuzzy matching) avant export, pour garantir
                      que Power BI puisse faire le lien.
    """
    diagnostiquer_dataset(df)
    df_std = adapter_dataset(df, col_zone=col_nom, col_valeur=col_valeur)
    
    if noms_reference:
        from adapter_dataset import normaliser_nom
        avant = df_std["zone"].copy()
        df_std["zone"] = df_std["zone"].apply(
            lambda x: normaliser_nom(x, noms_reference)
        )
        n_modifies = (avant != df_std["zone"]).sum()
        if n_modifies > 0:
            print(f"[powerbi] {n_modifies} nom(s) de zone réalignés sur le TopoJSON")
        
        # Vérification finale : tout est-il bien aligné ?
        non_trouves = set(df_std["zone"]) - set(noms_reference)
        if non_trouves:
            print(f"[powerbi] ⚠ {len(non_trouves)} zone(s) du CSV sans correspondance "
                  f"dans le TopoJSON : {non_trouves}")
            print("  → ces lignes n'apparaîtront pas sur la carte Power BI")
    
    df_std.to_csv(sortie, index=False, encoding="utf-8-sig")
    # encoding utf-8-sig : garantit que Power BI lit correctement les accents
    print(f"[ok] CSV exporté : {sortie} ({len(df_std)} lignes)")
    return df_std


# ─────────────────────────────────────────────────────────────────────────────
# 3. PIPELINE COMPLET D'EXPORT
# ─────────────────────────────────────────────────────────────────────────────

def exporter_pour_powerbi(
    niveau: str = "regions",
    df_metrique: pd.DataFrame = None,
    col_nom: str = None,
    col_valeur: str = None,
    dossier_sortie: str = "export_powerbi",
    simplification: float = None,
    zone_focus: str = None,
    col_filtre_focus: str = "NAME_1",
):
    """
    Pipeline complet : génère le TopoJSON et le CSV alignés, prêts à
    être importés dans Power BI Desktop.
    
    niveau          : 'regions', 'departements', 'arrondissements'
    df_metrique     : DataFrame de données (optionnel, sinon seul le
                       TopoJSON est généré — utile pour préparer la carte
                       avant d'avoir les données finales)
    dossier_sortie  : dossier où écrire les fichiers
    simplification  : tolérance de simplification géométrique (ex: 0.001),
                       recommandé pour 'arrondissements' (fichier plus léger)
    zone_focus      : si fourni, restreint l'export à une seule région
                       (ex: "Littoral") — utile pour préparer une carte
                       Power BI focalisée sur une zone précise
    col_filtre_focus: colonne GADM utilisée pour filtrer sur zone_focus
    """
    dossier = Path(dossier_sortie)
    dossier.mkdir(exist_ok=True)
    
    niveau_num = GADM_LEVELS.get(niveau)
    if niveau_num is None:
        raise ValueError(f"Niveau inconnu : {niveau}. Choisir parmi {list(GADM_LEVELS)}")
    
    # ── 1. Géométrie ──────────────────────────────────────────────────────────
    print(f"\n=== Export Power BI — niveau '{niveau}' ===\n")
    gdf = charger_gadm(niveau_num)
    col_nom_geo = f"NAME_{max(niveau_num, 1)}"
    
    if zone_focus:
        if col_filtre_focus not in gdf.columns:
            candidates = [c for c in gdf.columns if c.startswith("NAME_")]
            col_filtre_focus = candidates[0] if candidates else col_filtre_focus
        masque = gdf[col_filtre_focus].str.lower() == zone_focus.lower()
        if masque.sum() == 0:
            valeurs = gdf[col_filtre_focus].dropna().unique().tolist()
            raise ValueError(f"Zone '{zone_focus}' introuvable. Disponibles : {valeurs[:10]}")
        gdf = gdf[masque].copy()
        print(f"[focus] Export restreint à '{zone_focus}' — {len(gdf)} entité(s)")
    
    noms_reference = gdf[col_nom_geo].dropna().unique().tolist()
    
    # ── 2. TopoJSON ───────────────────────────────────────────────────────────
    chemin_topo = dossier / f"cameroun_{niveau}.topojson"
    try:
        topo_dict = geojson_vers_topojson(
            gdf, col_nom=col_nom_geo,
            nom_objet=niveau, simplification=simplification
        )
        with open(chemin_topo, "w", encoding="utf-8") as f:
            json.dump(topo_dict, f, ensure_ascii=False)
        print(f"[ok] TopoJSON exporté : {chemin_topo}")
    except ImportError as e:
        print(f"[attention] {e}")
        print("  → export du GeoJSON brut en repli (non optimal pour Power BI)")
        chemin_geo = dossier / f"cameroun_{niveau}.geojson"
        gdf[[col_nom_geo, "geometry"]].rename(
            columns={col_nom_geo: "nom"}
        ).to_crs(CRS_GLOBAL).to_file(chemin_geo, driver="GeoJSON")
        print(f"[ok] GeoJSON exporté en repli : {chemin_geo}")
        chemin_topo = None
    
    # ── 3. Données ────────────────────────────────────────────────────────────
    chemin_csv = None
    if df_metrique is not None:
        chemin_csv = dossier / "donnees_powerbi.csv"
        preparer_csv_powerbi(
            df_metrique, col_nom=col_nom, col_valeur=col_valeur,
            noms_reference=noms_reference, sortie=str(chemin_csv)
        )
    else:
        # Génère un CSV gabarit avec uniquement les noms de zones,
        # pour que l'utilisateur sache quel format remplir
        gabarit = pd.DataFrame({"zone": noms_reference, "valeur": None})
        chemin_csv = dossier / "gabarit_donnees.csv"
        gabarit.to_csv(chemin_csv, index=False, encoding="utf-8-sig")
        print(f"[ok] Gabarit de données généré : {chemin_csv}")
        print("  → remplissez la colonne 'valeur' avec votre métrique")
    
    # ── 4. Instructions ───────────────────────────────────────────────────────
    chemin_instructions = dossier / "INSTRUCTIONS_POWERBI.txt"
    instructions = _generer_instructions(niveau, chemin_topo, chemin_csv, niveau_num)
    chemin_instructions.write_text(instructions, encoding="utf-8")
    print(f"[ok] Instructions générées : {chemin_instructions}")
    
    print(f"\n=== Export terminé dans '{dossier}/' ===")
    print("Ouvrez INSTRUCTIONS_POWERBI.txt pour la suite (import dans Power BI Desktop).")
    
    return {
        "topojson": str(chemin_topo) if chemin_topo else None,
        "csv": str(chemin_csv),
        "instructions": str(chemin_instructions),
    }


def _generer_instructions(niveau: str, chemin_topo, chemin_csv, niveau_num: int) -> str:
    nom_objet = niveau
    return f"""INSTRUCTIONS — IMPORT DANS POWER BI DESKTOP
=============================================

Fichiers générés :
  - {chemin_topo if chemin_topo else '(GeoJSON en repli, voir ci-dessous)'}
  - {chemin_csv}

ÉTAPE 1 — Importer les données
  1. Power BI Desktop > Accueil > Obtenir les données > Texte/CSV
  2. Sélectionnez le fichier {Path(chemin_csv).name}
  3. Vérifiez que la colonne 'zone' est bien de type Texte (pas une catégorie
     géographique automatique — désactivez la détection de catégorie de données
     si Power BI propose 'Région du monde' ou similaire, sous peine de conflit
     avec le TopoJSON personnalisé)

ÉTAPE 2 — Ajouter le visuel Shape Map
  1. Dans le volet Visualisations, sélectionnez l'icône "Carte avec formes" (Shape Map)
     (si absente : Fichier > Options > Fonctionnalités en préversion > activer "Shape Map")
  2. Glissez le visuel sur le canevas

ÉTAPE 3 — Charger la carte personnalisée
  1. Sélectionnez le visuel Shape Map
  2. Volet Format (icône pinceau) > Forme de carte > Modifier
  3. Cliquez sur "+ Ajouter une carte"
  4. Importez le fichier {Path(chemin_topo).name if chemin_topo else '(votre fichier .topojson)'}

ÉTAPE 4 — Lier les données à la carte
  1. Champ "Emplacement" : glissez la colonne 'zone' de votre table
  2. Champ "Valeurs de couleur enregistrées" : glissez la colonne 'valeur'
  3. Power BI doit faire correspondre automatiquement le texte de 'zone' aux
     noms du TopoJSON (propriété 'nom' de chaque forme). Si la carte reste
     vide ou grise, vérifiez l'orthographe exacte dans le CSV — contrairement
     au pipeline Python, Power BI ne fait PAS de correction approximative
     des noms.

ÉTAPE 5 — Style et légende
  1. Volet Format > Couleurs des données : choisissez le dégradé
     (recommandé : cohérent avec votre charte — par ex. un dégradé
     séquentiel pour des taux, divergent pour des écarts à une moyenne)
  2. Volet Format > Étiquettes de données : activez si vous voulez les
     noms de zone visibles directement sur la carte

NIVEAU EXPORTÉ : {niveau} (GADM niveau {niveau_num})

POUR UN AUTRE NIVEAU OU UN ZOOM SUR UNE RÉGION :
  Relancez exporter_pour_powerbi() avec niveau="departements" ou
  zone_focus="NomDeLaRegion" pour générer un TopoJSON centré sur une
  zone précise (équivalent du drill_down() du pipeline Python).

LIMITES À CONNAÎTRE :
  - Power BI Shape Map ne fait pas de fuzzy matching : les noms doivent
    correspondre exactement entre le CSV et le TopoJSON. Ce script aligne
    déjà les deux automatiquement au moment de l'export — si vous modifiez
    le CSV après coup, gardez l'orthographe exacte des noms de zone.
  - Le drill-down interactif (cliquer une région pour voir ses départements)
    n'est pas nativement supporté par Shape Map. Pour cet effet, créez deux
    pages de rapport (une carte régions, une carte départements) reliées
    par une action de signet (bookmark) déclenchée au clic.
"""


# ─────────────────────────────────────────────────────────────────────────────
# 4. DÉMONSTRATION
# ─────────────────────────────────────────────────────────────────────────────

def demo_export():
    """Démo avec données simulées."""
    import numpy as np
    np.random.seed(0)
    
    df = pd.DataFrame({
        "region": [
            "Adamaoua", "Centre", "Est", "Extrême-Nord", "Littoral",
            "Nord", "Nord-Ouest", "Ouest", "Sud", "Sud-Ouest"
        ],
        "taux_pauvrete": np.random.uniform(20, 80, 10).round(1)
    })
    
    exporter_pour_powerbi(
        niveau="regions",
        df_metrique=df,
        dossier_sortie="export_powerbi_demo"
    )


if __name__ == "__main__":
    demo_export()
