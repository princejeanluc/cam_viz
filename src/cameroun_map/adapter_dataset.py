"""
adapter_dataset.py
===================
Module d'adaptation universelle des datasets vers le format standard
attendu par cameroun_viz.py : une ligne par (zone, [temps]) avec une
colonne 'zone' et une colonne 'valeur'.

Gère les cas réels les plus fréquents :
  1. Format large (une colonne par année) → conversion en format long
  2. Granularité mixte (mélange région/département dans le même fichier)
  3. Identifiants (codes INS/GADM) vs noms textuels
  4. Valeurs brutes vs métriques déjà calculées (taux, ratio)
  5. Séries temporelles (plusieurs dates)
  6. Doublons / variantes orthographiques d'une même zone

Usage rapide :

    from adapter_dataset import adapter_dataset, diagnostiquer_dataset

    diagnostiquer_dataset(df)              # comprendre ce qu'on a, sans rien changer
    df_propre = adapter_dataset(df)        # transformation automatique
"""

import pandas as pd
import numpy as np
import re

try:
    from thefuzz import process as fuzzy_process
    _FUZZY_DISPONIBLE = True
except ImportError:
    _FUZZY_DISPONIBLE = False


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTES DE DÉTECTION
# ─────────────────────────────────────────────────────────────────────────────

# Mots-clés permettant de repérer une colonne "zone géographique"
MOTS_CLES_ZONE = [
    "region", "région", "departement", "département", "dept",
    "arrondissement", "commune", "quartier", "ville", "zone",
    "province", "district", "localite", "localité"
]

# Mots-clés pour repérer une colonne "code" plutôt qu'un nom
MOTS_CLES_CODE = ["code", "id", "iso", "gadm", "ins_code", "cod_"]

# Motif d'une colonne-année en format large : "2020", "annee_2020", "Janvier 2024", "T1 2023"...
MOTIF_ANNEE = re.compile(r"^(annee_|year_|an_)?(\d{4})$")

MOIS_FR = (
    "janvier|février|fevrier|mars|avril|mai|juin|juillet|"
    "août|aout|septembre|octobre|novembre|décembre|decembre"
)
MOTIF_MOIS_ANNEE = re.compile(rf"^({MOIS_FR})\s+(\d{{4}})$", re.IGNORECASE)
MOTIF_TRIMESTRE  = re.compile(r"^(t|trim|q)[1-4]\s*[-_ ]?\s*(\d{4})$", re.IGNORECASE)

REGIONS_OFFICIELLES = [
    "Adamaoua", "Centre", "Est", "Extrême-Nord",
    "Littoral", "Nord", "Nord-Ouest", "Ouest",
    "Sud", "Sud-Ouest"
]


# ─────────────────────────────────────────────────────────────────────────────
# 1. DÉTECTION DE LA STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

def detecter_colonne_zone(df: pd.DataFrame) -> str | None:
    """Devine quelle colonne contient le nom de la zone géographique."""
    for col in df.columns:
        col_norm = col.lower().strip()
        if any(mot in col_norm for mot in MOTS_CLES_ZONE):
            return col
    
    # Repli : colonne texte avec peu de valeurs uniques par rapport au nombre de lignes
    for col in df.select_dtypes(include="object").columns:
        n_unique = df[col].nunique()
        if 3 <= n_unique <= 400:   # plausible pour régions/départements/arrondissements
            return col
    return None


def detecter_colonne_code(df: pd.DataFrame) -> str | None:
    """Détecte une colonne d'identifiant/code, à privilégier pour la jointure."""
    for col in df.columns:
        col_norm = col.lower().strip()
        if any(mot in col_norm for mot in MOTS_CLES_CODE):
            return col
    return None


def detecter_colonnes_annees(df: pd.DataFrame) -> list:
    """
    Détecte les colonnes qui représentent une période temporelle en format large :
    années pures (2020), avec préfixe (annee_2020), mois+année (Janvier 2024),
    ou trimestres (T1 2023).
    """
    cols_temps = []
    for col in df.columns:
        col_str = str(col).strip()
        if MOTIF_ANNEE.match(col_str) or MOTIF_MOIS_ANNEE.match(col_str) or MOTIF_TRIMESTRE.match(col_str):
            cols_temps.append(col)
    return cols_temps


def detecter_colonne_temps(df: pd.DataFrame) -> str | None:
    """Détecte une colonne temporelle déjà en format long (ex: 'annee', 'date', 'mois')."""
    mots_temps = ["annee", "année", "year", "date", "mois", "month", "periode", "période", "trimestre"]
    for col in df.columns:
        if col.lower().strip() in mots_temps:
            return col
    return None


def detecter_niveau_administratif(
    valeurs_zone: pd.Series,
    geo_regions: list = None,
    geo_departements: list = None
) -> pd.Series:
    """
    Pour chaque valeur de zone, devine à quel niveau administratif elle appartient
    (régions vs départements) en comparant aux listes de référence.
    
    Utile pour le cas 'granularité mixte' où certaines lignes sont des régions
    et d'autres des départements dans le MÊME fichier.
    """
    geo_regions = geo_regions or REGIONS_OFFICIELLES
    geo_departements = geo_departements or []
    
    def classer(val):
        if pd.isna(val):
            return "inconnu"
        val_str = str(val).strip().lower()
        if any(val_str == r.lower() for r in geo_regions):
            return "region"
        if geo_departements and any(val_str == d.lower() for d in geo_departements):
            return "departement"
        return "inconnu"
    
    return valeurs_zone.apply(classer)


def detecter_colonnes_valeur(df: pd.DataFrame, col_zone: str, col_code: str = None) -> list:
    """Retourne les colonnes numériques candidates comme métrique à cartographier."""
    exclues = {col_zone, col_code}
    candidates = [
        c for c in df.select_dtypes(include=[np.number]).columns
        if c not in exclues
    ]
    return candidates


_AVERTI_FUZZY_ABSENT = False  # affiché une seule fois par session


def normaliser_nom_avec_score(nom: str, candidats: list, seuil: int = 70) -> dict:
    """
    Comme normaliser_nom(), mais retourne le détail de la correspondance
    plutôt que seulement le nom final. Sert de base à carte.valider() pour
    construire une piste de confiance sur chaque jointure (voir CONTEXTE_PROJET.md).

    Retourne un dict :
        nom_origine   : le nom tel que fourni en entrée
        nom_normalise : le nom retenu après normalisation
        score         : 100 si correspondance exacte, score thefuzz sinon,
                        None si aucune correspondance fiable trouvée
        methode       : "exact" | "fuzzy" | "non_trouve" | "fuzzy_absent"
    """
    global _AVERTI_FUZZY_ABSENT

    if pd.isna(nom) or str(nom).strip() == "":
        return {"nom_origine": nom, "nom_normalise": nom, "score": None, "methode": "vide"}

    nom_clean = str(nom).strip()

    # Correspondance exacte d'abord (insensible à la casse)
    for c in candidats:
        if c.lower() == nom_clean.lower():
            return {"nom_origine": nom_clean, "nom_normalise": c, "score": 100, "methode": "exact"}

    if not _FUZZY_DISPONIBLE:
        if not _AVERTI_FUZZY_ABSENT:
            print(
                "  [warn] 'thefuzz' non installé — les noms mal orthographiés "
                "ne seront PAS corrigés automatiquement (pip install thefuzz "
                "python-Levenshtein)."
            )
            _AVERTI_FUZZY_ABSENT = True
        return {"nom_origine": nom_clean, "nom_normalise": nom_clean, "score": None, "methode": "fuzzy_absent"}

    match, score = fuzzy_process.extractOne(nom_clean, candidats)
    if score >= seuil:
        return {"nom_origine": nom_clean, "nom_normalise": match, "score": score, "methode": "fuzzy"}

    print(f"  [warn] '{nom_clean}' → aucune correspondance fiable (meilleur: '{match}' {score}%)")
    return {"nom_origine": nom_clean, "nom_normalise": nom_clean, "score": score, "methode": "non_trouve"}


def normaliser_nom(nom: str, candidats: list, seuil: int = 70) -> str:
    """
    Aligne un nom de zone sur le candidat officiel le plus proche
    (fuzzy matching), pour corriger les variantes orthographiques.

    Exemple :
        normaliser_nom("extreme nord", REGIONS_OFFICIELLES) → "Extrême-Nord"

    Si 'thefuzz' n'est pas installé, retourne le nom inchangé et affiche
    un avertissement (une seule fois par session) — aucune erreur bloquante.
    Si le score de correspondance est sous le seuil, retourne le nom original.

    Pour récupérer le score et la méthode de correspondance (utilisé par
    carte.valider()), voir normaliser_nom_avec_score().
    """
    return normaliser_nom_avec_score(nom, candidats, seuil)["nom_normalise"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. DIAGNOSTIC — comprendre le dataset SANS le modifier
# ─────────────────────────────────────────────────────────────────────────────

def diagnostiquer_dataset(df: pd.DataFrame, geo_departements: list = None) -> dict:
    """
    Analyse un dataset brut et affiche un rapport :
    - colonnes identifiées (zone, code, temps, valeur)
    - format détecté (large ou long)
    - granularité (homogène ou mixte)
    - doublons potentiels
    
    Ne modifie rien — sert à décider quoi faire avant adapter_dataset().
    """
    print("\n" + "═" * 60)
    print("  DIAGNOSTIC DU DATASET")
    print("═" * 60)
    print(f"  Dimensions : {df.shape[0]} lignes × {df.shape[1]} colonnes")
    print(f"  Colonnes   : {list(df.columns)}\n")
    
    rapport = {}
    
    # ── Colonne zone ──────────────────────────────────────────────────────────
    col_zone = detecter_colonne_zone(df)
    rapport["col_zone"] = col_zone
    if col_zone:
        n_unique = df[col_zone].nunique()
        print(f"  ✓ Colonne zone détectée : '{col_zone}' ({n_unique} valeurs uniques)")
    else:
        print("  ✗ Aucune colonne de zone géographique détectée — à préciser manuellement")
    
    # ── Colonne code ──────────────────────────────────────────────────────────
    col_code = detecter_colonne_code(df)
    rapport["col_code"] = col_code
    if col_code:
        print(f"  ✓ Colonne code/identifiant détectée : '{col_code}' (jointure plus fiable que par nom)")
    
    # ── Format large vs long ────────────────────────────────────────────────────
    cols_annees = detecter_colonnes_annees(df)
    col_temps = detecter_colonne_temps(df)
    rapport["cols_annees"] = cols_annees
    rapport["col_temps"] = col_temps
    
    if cols_annees:
        print(f"  ⚠ Format LARGE détecté : colonnes-années {cols_annees}")
        print(f"    → nécessite une conversion en format long (voir adapter_dataset)")
        rapport["format"] = "large"
    elif col_temps:
        print(f"  ✓ Format LONG avec colonne temporelle : '{col_temps}'")
        rapport["format"] = "long_temporel"
    else:
        print(f"  ✓ Format LONG simple (une ligne par zone, pas de dimension temporelle)")
        rapport["format"] = "long_simple"
    
    # ── Granularité ───────────────────────────────────────────────────────────
    if col_zone:
        niveaux = detecter_niveau_administratif(df[col_zone], geo_departements=geo_departements)
        compte_niveaux = niveaux.value_counts().to_dict()
        rapport["niveaux_detectes"] = compte_niveaux
        
        if len(compte_niveaux) > 1 and "inconnu" not in compte_niveaux:
            print(f"  ⚠ GRANULARITÉ MIXTE détectée : {compte_niveaux}")
            print(f"    → séparez les niveaux avant de cartographier (voir separer_par_niveau)")
        elif compte_niveaux.get("inconnu", 0) > 0:
            n_inconnu = compte_niveaux["inconnu"]
            print(f"  ℹ {n_inconnu} valeur(s) de zone non reconnues (normal si niveau département/arrondissement)")
    
    # ── Doublons ──────────────────────────────────────────────────────────────
    if col_zone:
        cle_dup = [col_zone] + ([col_temps] if col_temps else [])
        doublons = df[df.duplicated(subset=cle_dup, keep=False)]
        rapport["n_doublons"] = len(doublons)
        if len(doublons) > 0:
            zones_dup = doublons[col_zone].unique().tolist()
            print(f"  ⚠ {len(doublons)} ligne(s) en doublon pour : {zones_dup}")
            print(f"    → agrégation nécessaire avant jointure (somme/moyenne ?)")
    
    # ── Colonnes valeur candidates ───────────────────────────────────────────────
    cols_valeur = detecter_colonnes_valeur(df, col_zone, col_code)
    rapport["cols_valeur_candidates"] = cols_valeur
    if cols_valeur:
        print(f"\n  Colonnes numériques disponibles comme métrique : {cols_valeur}")
    
    print("═" * 60 + "\n")
    return rapport


# ─────────────────────────────────────────────────────────────────────────────
# 3. TRANSFORMATION — large → long
# ─────────────────────────────────────────────────────────────────────────────

def large_vers_long(
    df: pd.DataFrame,
    col_zone: str,
    cols_annees: list = None,
    nom_colonne_temps: str = "annee",
    nom_colonne_valeur: str = "valeur"
) -> pd.DataFrame:
    """
    Convertit un dataset au format large (une colonne par année)
    en format long (une ligne par zone × année).
    
    Avant :
        region    | 2020 | 2021 | 2022
        Littoral  | 12.3 | 14.1 | 15.0
    
    Après :
        region    | annee | valeur
        Littoral  | 2020  | 12.3
        Littoral  | 2021  | 14.1
        Littoral  | 2022  | 15.0
    """
    if cols_annees is None:
        cols_annees = detecter_colonnes_annees(df)
    
    if not cols_annees:
        raise ValueError("Aucune colonne-année détectée. Précisez cols_annees explicitement.")
    
    autres_cols = [c for c in df.columns if c not in cols_annees]
    
    df_long = df.melt(
        id_vars=autres_cols,
        value_vars=cols_annees,
        var_name=nom_colonne_temps,
        value_name=nom_colonne_valeur
    )
    
    # Nettoyer les libellés temporels (espaces superflus), sans perdre le mois
    df_long[nom_colonne_temps] = df_long[nom_colonne_temps].apply(
        lambda x: str(x).strip()
    )
    
    print(f"[transform] Format large → long : {len(df)} lignes → {len(df_long)} lignes")
    return df_long


# ─────────────────────────────────────────────────────────────────────────────
# 4. SÉPARATION PAR NIVEAU (granularité mixte)
# ─────────────────────────────────────────────────────────────────────────────

def separer_par_niveau(
    df: pd.DataFrame,
    col_zone: str,
    geo_regions: list = None,
    geo_departements: list = None
) -> dict:
    """
    Sépare un dataset à granularité mixte en plusieurs DataFrames,
    un par niveau administratif détecté.
    
    Retourne : {"region": df_regions, "departement": df_departements, "inconnu": df_reste}
    """
    niveaux = detecter_niveau_administratif(df[col_zone], geo_regions, geo_departements)
    resultat = {}
    for niveau in niveaux.unique():
        resultat[niveau] = df[niveaux == niveau].copy()
        print(f"[split] Niveau '{niveau}' : {len(resultat[niveau])} lignes")
    return resultat


# ─────────────────────────────────────────────────────────────────────────────
# 5. AGRÉGATION DES DOUBLONS
# ─────────────────────────────────────────────────────────────────────────────

def agreger_doublons(
    df: pd.DataFrame,
    col_zone: str,
    col_valeur: str,
    col_temps: str = None,
    methode: str = "moyenne"
) -> pd.DataFrame:
    """
    Agrège les lignes en doublon (même zone, même période) selon la méthode choisie.
    methode : 'moyenne', 'somme', 'max', 'min', 'premier'
    """
    cle = [col_zone] + ([col_temps] if col_temps else [])
    
    fonctions = {
        "moyenne": "mean", "somme": "sum",
        "max": "max", "min": "min", "premier": "first"
    }
    if methode not in fonctions:
        raise ValueError(f"Méthode inconnue : {methode}. Choisir parmi {list(fonctions)}")
    
    n_avant = len(df)
    df_agg = df.groupby(cle, as_index=False)[col_valeur].agg(fonctions[methode])
    
    print(f"[agregation] {n_avant} lignes → {len(df_agg)} lignes (méthode: {methode})")
    return df_agg


# ─────────────────────────────────────────────────────────────────────────────
# 6. NORMALISATION PAR HABITANT / RATIO
# ─────────────────────────────────────────────────────────────────────────────

def normaliser_par_population(
    df: pd.DataFrame,
    col_valeur_brute: str,
    col_population: str,
    pour: int = 100_000,
    nom_resultat: str = None
) -> pd.DataFrame:
    """
    Convertit une valeur brute (ex: nombre de cas) en taux par habitant.
    Évite le biais classique : une grande région a souvent plus de cas
    en valeur absolue simplement parce qu'elle a plus d'habitants.
    
    Exemple : normaliser_par_population(df, "cas_palu", "population", pour=100_000)
              → ajoute une colonne 'cas_palu_pour_100000'
    """
    df = df.copy()
    nom_resultat = nom_resultat or f"{col_valeur_brute}_pour_{pour}"
    df[nom_resultat] = (df[col_valeur_brute] / df[col_population]) * pour
    print(f"[normalisation] Colonne '{nom_resultat}' créée (taux pour {pour:,} habitants)")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# 7. PIPELINE PRINCIPAL — adaptation automatique
# ─────────────────────────────────────────────────────────────────────────────

def adapter_dataset(
    df: pd.DataFrame,
    col_zone: str = None,
    col_valeur: str = None,
    col_code: str = None,
    geo_departements: list = None,
    methode_agregation_doublons: str = "moyenne",
    verbeux: bool = True
) -> pd.DataFrame:
    """
    Pipeline d'adaptation automatique vers le format standard :
    une ligne par (zone, [temps]) avec colonnes 'zone' et 'valeur'.
    
    Détecte et corrige automatiquement :
      - format large → long
      - doublons → agrégés
    
    Ne résout PAS automatiquement (nécessite intervention manuelle) :
      - granularité mixte (utilisez separer_par_niveau en amont)
      - choix entre valeur brute et taux normalisé (utilisez normaliser_par_population)
    
    Retourne un DataFrame avec au minimum les colonnes 'zone' et 'valeur',
    et 'temps' si une dimension temporelle est détectée.
    """
    df = df.copy()
    
    # ── Détection des colonnes si non fournies ───────────────────────────────
    col_zone = col_zone or detecter_colonne_zone(df)
    col_code = col_code or detecter_colonne_code(df)
    
    if col_zone is None:
        raise ValueError(
            "Impossible de détecter la colonne géographique. "
            "Précisez-la avec adapter_dataset(df, col_zone='votre_colonne')."
        )
    
    if verbeux:
        print(f"[adapter] Colonne zone utilisée : '{col_zone}'")
    
    # ── Format large → long si nécessaire ─────────────────────────────────────
    cols_annees = detecter_colonnes_annees(df)
    col_temps = detecter_colonne_temps(df)
    
    if cols_annees:
        df = large_vers_long(df, col_zone, cols_annees)
        col_valeur = col_valeur or "valeur"
        col_temps = "annee"
    else:
        if col_valeur is None:
            candidates = detecter_colonnes_valeur(df, col_zone, col_code)
            if len(candidates) == 1:
                col_valeur = candidates[0]
                if verbeux:
                    print(f"[adapter] Colonne valeur déduite : '{col_valeur}'")
            elif len(candidates) > 1:
                raise ValueError(
                    f"Plusieurs colonnes numériques possibles : {candidates}. "
                    f"Précisez avec adapter_dataset(df, col_valeur='...')."
                )
            else:
                raise ValueError("Aucune colonne numérique détectée pour la métrique.")
    
    # ── Renommage standard ────────────────────────────────────────────────────
    colonnes_finales = {col_zone: "zone", col_valeur: "valeur"}
    if col_temps:
        colonnes_finales[col_temps] = "temps"
    if col_code:
        colonnes_finales[col_code] = "code"
    
    df_std = df.rename(columns=colonnes_finales)
    cols_a_garder = list(colonnes_finales.values())
    df_std = df_std[[c for c in cols_a_garder if c in df_std.columns]]
    
    # ── Conversion de type pour 'valeur' ──────────────────────────────────────
    df_std["valeur"] = pd.to_numeric(df_std["valeur"], errors="coerce")
    
    # ── Agrégation des doublons ───────────────────────────────────────────────
    cle = ["zone"] + (["temps"] if "temps" in df_std.columns else [])
    n_doublons = df_std.duplicated(subset=cle, keep=False).sum()
    if n_doublons > 0:
        if verbeux:
            print(f"[adapter] {n_doublons} doublon(s) détecté(s) → agrégation par '{methode_agregation_doublons}'")
        df_std = agreger_doublons(
            df_std, "zone", "valeur",
            col_temps="temps" if "temps" in df_std.columns else None,
            methode=methode_agregation_doublons
        )
    
    if verbeux:
        n_valides = df_std["valeur"].notna().sum()
        print(f"[adapter] Terminé : {len(df_std)} lignes, {n_valides} valeurs numériques valides")
    
    return df_std


# ─────────────────────────────────────────────────────────────────────────────
# 8. DÉMONSTRATION DES CAS DIFFICILES
# ─────────────────────────────────────────────────────────────────────────────

def demo_cas_difficiles():
    """Illustre chaque cas difficile avec un exemple minimal."""
    
    print("\n### CAS 1 : Format large (une colonne par année) ###")
    df_large = pd.DataFrame({
        "region": ["Littoral", "Centre", "Ouest"],
        "2020": [12.3, 8.1, 15.4],
        "2021": [14.1, 9.0, 16.2],
        "2022": [15.0, 9.8, 17.1],
    })
    print(df_large)
    diagnostiquer_dataset(df_large)
    df_propre = adapter_dataset(df_large)
    print(df_propre)
    
    print("\n### CAS 2 : Doublons (deux mesures pour Littoral) ###")
    df_doublons = pd.DataFrame({
        "region": ["Littoral", "Littoral", "Centre"],
        "taux": [12.3, 13.1, 8.1],
    })
    print(df_doublons)
    diagnostiquer_dataset(df_doublons)
    df_propre2 = adapter_dataset(df_doublons, methode_agregation_doublons="moyenne")
    print(df_propre2)
    
    print("\n### CAS 3 : Valeur brute vs population (biais) ###")
    df_brut = pd.DataFrame({
        "region": ["Littoral", "Adamaoua"],
        "cas_palu": [50000, 8000],
        "population": [3500000, 900000],
    })
    df_norm = normaliser_par_population(df_brut, "cas_palu", "population", pour=100_000)
    print(df_norm)
    print("→ Littoral semble avoir plus de cas en absolu, mais le taux pour 100k habitants")
    print("  donne une image différente de la sévérité réelle.")


if __name__ == "__main__":
    demo_cas_difficiles()
