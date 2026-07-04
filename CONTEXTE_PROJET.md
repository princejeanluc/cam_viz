# Contexte projet — cameroun_viz

**Document destiné à Claude Code ou à un nouveau contexte Claude.**
Ce document décrit l'état exact du projet, les décisions de conception prises,
les bugs connus, et ce qui reste à faire. Il doit être lu en entier avant
toute modification du code.

---

## 1. Ce qu'est ce projet

Une librairie Python de visualisation cartographique du Cameroun, utilisable
dans deux contextes : un script Python autonome, ou un notebook Jupyter.
Elle n'est pas destinée à être une application web, un serveur, ou un service.

L'objectif central : permettre à quelqu'un avec un CSV de données (même mal
structuré, même avec des noms de zones mal orthographiés) de produire une carte
choroplèthe du Cameroun en moins de 5 lignes de code.

Ce n'est pas un projet académique de SIG. C'est un outil pratique, orienté
analyste / data scientist francophone travaillant sur le Cameroun.

---

## 2. Architecture : trois fichiers, trois responsabilités

```
camviz/
├── pyproject.toml         → packaging, dépendances + extras (fuzzy, powerbi, osm, dev)
├── requirements.txt       → liste à plat, conservée pour install rapide hors pyproject
├── src/
│   └── cameroun_map/
│       ├── __init__.py        → API publique (réexporte les symboles des 3 modules)
│       ├── cameroun_viz.py    → moteur principal
│       ├── adapter_dataset.py → nettoyage des données brutes
│       └── export_powerbi.py  → export TopoJSON + CSV pour Power BI
├── examples/
│   └── exemples_utilisation.py
├── tests/
│   └── test_rapide.py     → test autonome sans données réelles
└── data/                  → cache GADM (créé automatiquement, gitignored)
```

Le projet est installable avec `pip install -e .` depuis la racine (`camviz/`).
Les imports internes entre les trois modules sont relatifs (`from .adapter_dataset
import ...`) — ne pas revenir à des imports absolus (`from adapter_dataset import
...`), ils casseraient une fois le package installé.

### cameroun_viz.py (1 230 lignes)

Contient :
- Les fonctions de chargement géographique (`charger_gadm`, `charger_osm_quartiers`)
- Les fonctions de rendu (`carte_statique`, `carte_interactive_folium`, `carte_plotly`)
- La classification des valeurs (`classifier_valeurs`)
- La jointure données ↔ géométrie (`joindre_metrique`)
- La classe principale `CarteCameroun`
- La fonction de rendu avec contexte `_carte_avec_contexte` (préfixe `_` = privée)

### adapter_dataset.py (558 lignes)

Contient toute la logique de détection et de nettoyage des données brutes.
Peut être utilisé **indépendamment** de la cartographie.
Fonctions importantes :
- `diagnostiquer_dataset(df)` — rapport texte, ne modifie rien
- `adapter_dataset(df)` — pipeline complet, retourne un DataFrame standardisé
- `normaliser_nom(nom, candidats)` — fuzzy matching d'un nom de zone
- `large_vers_long(df, col_zone)` — conversion format large → long
- `separer_par_niveau(df, col_zone)` — sépare granularité mixte
- `normaliser_par_population(df, col_brute, col_pop)` — calcule un taux

### export_powerbi.py (336 lignes)

Génère deux fichiers pour le visuel Shape Map de Power BI Desktop :
- Un TopoJSON des contours (nécessite le package `topojson`)
- Un CSV avec les noms de zones alignés exactement sur le TopoJSON
Point critique : Power BI ne fait pas de fuzzy matching — les noms doivent
correspondre exactement. Ce module fait cet alignement à l'export.

---

## 3. Dépendances entre modules

```
export_powerbi.py
    ↓ importe
    cameroun_viz.py  (charger_gadm, GADM_LEVELS, CRS_GLOBAL)
    adapter_dataset.py  (diagnostiquer_dataset, adapter_dataset)

cameroun_viz.py
    ↓ importe
    adapter_dataset.py  (diagnostiquer_dataset, adapter_dataset,
                          detecter_colonne_zone, detecter_colonnes_valeur,
                          normaliser_nom)

adapter_dataset.py
    → aucune dépendance interne (autonome)
```

`adapter_dataset.py` est la feuille de l'arbre de dépendances.
Il ne doit jamais importer depuis `cameroun_viz.py` (créerait une
dépendance circulaire).

---

## 4. Classe CarteCameroun — interface principale

```python
carte = CarteCameroun(niveau="regions")  # ou departements, arrondissements,
                                          # arrondissements, pays, osm_quartiers
carte.charger_geo()
carte.charger_metrique(df)               # détecte et adapte automatiquement
carte.visualiser(titre="...", sortie="carte.png")
carte.visualiser_interactif(moteur="folium", sortie="carte.html")
```

**Méthodes de la classe (ordre logique d'utilisation) :**

| Méthode | Rôle |
|---|---|
| `__init__(niveau)` | Initialise, valide le niveau |
| `charger_geo(ville_osm, forcer)` | Charge GADM ou OSM selon le niveau |
| `charger_metrique(df, col_nom, col_valeur, auto_adapter, diagnostic)` | Jointure données ↔ géométrie |
| `diagnostiquer()` | Affiche un résumé de l'état de la jointure |
| `valider(seuil_alerte=85)` | Affiche/retourne la table de correspondance nom CSV ↔ zone géo, avec score et méthode (exact/fuzzy), et signale les correspondances fuzzy sous le seuil |
| `focus(zone, col_parent)` | Mode A zoom : filtre sur une zone, même niveau |
| `drill_down(zone, niveau_interieur, ...)` | Mode B zoom : change de niveau à l'intérieur d'une zone |
| `visualiser(titre, unite, methode, n_classes, schema_couleur, afficher_labels, afficher_contexte, sortie)` | Carte statique PNG/SVG |
| `visualiser_interactif(titre, moteur, unite, methode, n_classes, schema_couleur, sortie)` | Carte interactive Folium ou Plotly |

**Attributs internes de la classe :**

| Attribut | Type | Rôle |
|---|---|---|
| `self.niveau` | str | Niveau administratif courant |
| `self.gdf` | GeoDataFrame | Géométrie brute (sans données) |
| `self.gdf_joint` | GeoDataFrame | Géométrie + données métriques |
| `self._col_nom` | str | Nom de la colonne de zone dans le GDF (ex: NAME_1) |
| `self._col_valeur` | str | Nom de la colonne métrique active |
| `self._zone_focus` | str | Défini par focus() ou drill_down(), sinon absent |
| `self._gdf_contexte` | GeoDataFrame | Carte parente complète pour le fond grisé |

**focus() et drill_down() retournent un nouvel objet CarteCameroun** (pas
self modifié). L'original reste intact. Le clone porte `_zone_focus` et
`_gdf_contexte`, ce qui déclenche automatiquement le rendu avec fond grisé
dans `visualiser()`.

---

## 5. Niveaux administratifs disponibles

| Argument `niveau=` | GADM num | Zones | Source |
|---|---|---|---|
| `"pays"` | 0 | 1 | GADM |
| `"regions"` | 1 | 10 | GADM |
| `"departements"` | 2 | 58 | GADM |
| `"arrondissements"` | 3 | ~360 | GADM |
| `"osm_quartiers"` | N/A | variable | OSM Overpass |

Colonne de nom dans le GeoDataFrame GADM : `NAME_{niveau_num}` (ex: `NAME_1`
pour les régions, `NAME_2` pour les départements).

Pour `osm_quartiers`, la colonne de nom est `"nom"`.

---

## 6. Pipeline de données — ce qui se passe dans charger_metrique()

```
df brut (format quelconque)
    ↓ diagnostiquer_dataset(df)       → rapport affiché, rien modifié
    ↓ adapter_dataset(df)             → DataFrame standardisé {zone, valeur, [temps]}
    ↓ joindre_metrique(gdf, df_std)   → GeoDataFrame avec colonnes
                                         {valeur, manquant, _nom_origine,
                                          _match_score, _match_methode}
    → self.gdf_joint
```

`joindre_metrique` trace désormais, pour chaque zone géo, le nom d'origine
du CSV (`_nom_origine`), la méthode de correspondance (`exact` / `fuzzy` /
`exact_only` si fuzzy désactivé) et le score (`_match_score`, 0-100). Si deux
noms d'origine distincts se résolvent fuzzy-vers la même zone géo (ex: "Nord"
et "Nord-Ouest" matchant tous deux "Nord-Ouest"), c'est une collision : le
merge gauche dupliquerait sinon la zone dans le résultat. `joindre_metrique`
détecte ce cas, imprime une alerte `[join][ALERTE]`, et fusionne les valeurs
par moyenne pour garder une seule ligne par zone géographique.

`CarteCameroun.valider(seuil_alerte=85)` consomme ces colonnes pour afficher
une table de correspondance ligne par ligne et signaler les correspondances
fuzzy sous le seuil — c'est la réponse au problème de confiance identifié
dans la conversation du 2026-06-28 (un fuzzy match incorrect produit une
carte qui a l'air juste sans l'être). À appeler après `charger_metrique()`,
avant de faire confiance à `visualiser()`.

`adapter_dataset()` gère automatiquement :
- Format large (colonnes par année/mois/trimestre) → long (melt)
- Doublons (agrégation par moyenne par défaut)
- Détection automatique des colonnes si non spécifiées

`adapter_dataset()` ne gère PAS (décision intentionnelle, choix métier) :
- Granularité mixte → utiliser `separer_par_niveau()` en amont
- Normalisation par habitant → utiliser `normaliser_par_population()` en amont

---

## 7. Détection de format dans adapter_dataset

Colonnes temporelles reconnues comme format large (déclenchent un melt) :
- Année pure : `2020`, `2021`
- Avec préfixe : `annee_2020`, `year_2020`, `an_2020`
- Mois + année : `Janvier 2024`, `Février 2024` (regex, insensible à la casse)
- Trimestre : `T1 2023`, `Q2 2024`, `trim1 2023`

Colonnes "zone géographique" reconnues par mots-clés dans le nom de colonne :
`region`, `région`, `departement`, `département`, `dept`, `arrondissement`,
`commune`, `quartier`, `ville`, `zone`, `province`, `district`, `localite`.

Repli si aucun mot-clé trouvé : première colonne objet avec entre 3 et 400
valeurs uniques (heuristique).

---

## 8. Dépendances optionnelles — comportement si absentes

| Package | Usage | Comportement si absent |
|---|---|---|
| `thefuzz` + `python-Levenshtein` | Fuzzy matching des noms de zone | Avertissement affiché 1 fois, correspondance exacte seulement |
| `mapclassify` | Classification Jenks | Bascule silencieuse sur quantile |
| `topojson` | Export Power BI | Erreur explicite avec message d'installation |
| `osmnx` | Données urbaines fines | Non importé, fonctionnalité non disponible |

---

## 9. Projections cartographiques

- `CRS_LOCAL = "EPSG:32632"` — UTM zone 32N, en mètres. Utilisé pour
  tous les rendus Matplotlib (précision géométrique, barre d'échelle exacte).
- `CRS_GLOBAL = "EPSG:4326"` — WGS84, en degrés. Utilisé pour Folium,
  Plotly, et l'export Power BI (ces outils l'imposent).

Toutes les fonctions de rendu font la reprojection en interne —
l'utilisateur n'a jamais à s'en préoccuper.

---

## 10. Bugs connus et dette technique

### Bug réseau corrigé (session 2026-06-28)
`charger_gadm()` levait un `ConnectionError` non récupérable au moindre
`ConnectionResetError` pendant le handshake TLS avec geodata.ucdavis.edu —
observé dans `examples/galerie.ipynb` en environnement Windows réel (pare-feu
ou antivirus qui inspecte le HTTPS, ou instabilité côté serveur GADM).
Corrigé en routant la requête via une `requests.Session` avec
`urllib3.Retry(total=4, backoff_factor=1.5)` (fonction `_session_avec_retries`
dans `cameroun_viz.py`). Le message d'erreur final (si les 4 tentatives
échouent) reste explicite et pointe vers le téléchargement manuel.

### Bug critique corrigé dans cette session
`visualiser()` et `visualiser_interactif()` étaient définies deux fois dans
la classe `CarteCameroun`. Python garde silencieusement la dernière définition.
La première (sans `afficher_contexte`) était morte. Corrigé : les deux
premières définitions ont été supprimées.

### Dette technique identifiée (pas encore corrigée)

**1. Pas de tests**
Zéro test unitaire. Chaque modification est vérifiée uniquement par inspection
visuelle et exécution manuelle. Priorité absolue de la phase 1.
Fichiers à tester en priorité : `adapter_dataset.py` (logique pure, facile à
tester), puis `joindre_metrique` et `classifier_valeurs` dans `cameroun_viz.py`.

**2. Tous les logs sont des `print()`**
Impossible à désactiver, filtrer, ou rediriger. En notebook Jupyter, pollue
les outputs de cellule. À remplacer par `logging.getLogger("cameroun_viz")`.

**3. Pas de cache intelligent**
Le cache GADM est basé sur le nom de fichier fixe
`data/cameroun_gadm_niveau{N}.geojson`. Si `zone_focus` ou `simplification`
changent, le cache retourne le mauvais résultat sans avertissement.
Solution : hasher les paramètres dans le nom du fichier cache.

**4. Pas de package installable**
Les fichiers doivent être dans le même répertoire que le script utilisateur.
Pas de `pyproject.toml`, pas d'`__init__.py`. Partager le projet = copier
un dossier manuellement.

**5. `_AVERTI_FUZZY_ABSENT` est une variable globale**
Dans `adapter_dataset.py`, cette variable contrôle l'affichage unique de
l'avertissement thefuzz. En environnement multi-thread ou si le module est
rechargé, son comportement devient imprévisible. À remplacer par
`warnings.warn(..., stacklevel=2)` avec un filtre `once`.

**6. `charger_osm_quartiers` est fragile**
La reconstruction de polygones depuis les membres de relations OSM est une
approximation. Les membres `inner` (trous dans les polygones) sont ignorés.
Les résultats peuvent être géométriquement incorrects sur des zones complexes.

---

## 11. Ce qui n'existe pas encore — roadmap priorisée

### Phase 1 — Fondations (à faire avant d'ajouter des fonctionnalités)

- **Tests unitaires (pytest)** — priorité absolue. Couvrir :
  `detecter_colonnes_annees`, `large_vers_long`, `agreger_doublons`,
  `normaliser_nom`, `classifier_valeurs`, `joindre_metrique`.
  Un test doit produire une assertion vérifiable sans données réseau
  (utiliser des GeoDataFrames fabriqués à la main avec Shapely).

- **Logging structuré** — remplacer tous les `print()` par
  `logger = logging.getLogger("cameroun_viz")`. Permettre à l'utilisateur
  de contrôler le verbosity avec `logging.setLevel(logging.WARNING)`.

- **Cache intelligent** — nommer les fichiers cache avec un hash des
  paramètres (niveau, zone_focus, simplification). Utiliser `hashlib.md5`.

- **Package pip** — ajouter `__init__.py` et `pyproject.toml` pour permettre
  `pip install -e .` depuis le répertoire du projet.

### Phase 2 — Analyse spatiale

- **Slider temporel** — la colonne `temps` est déjà gérée par `adapter_dataset`
  mais le rendu ne sait pas la consommer. Implémenter avec
  `folium.plugins.TimestampedGeoJson` pour Folium, et `px.choropleth` avec
  `animation_frame` pour Plotly.

- **Clusters LISA / hot spots** — ajouter une méthode `analyser_clusters()`
  sur `CarteCameroun` utilisant `esda.Moran` et `esda.Moran_Local`
  (package `esda` de PySAL). Retourner une carte avec 4 catégories :
  High-High, Low-Low, High-Low, Low-High, Non significatif.

- **Carte bivariée** — deux métriques simultanées sur une grille de couleurs
  3×3. Package `pysal` ou implémentation manuelle avec une palette de 9 cases.

### Phase 3 — Expérience utilisateur

- **config.yaml par projet** — charger automatiquement un fichier
  `cameroun_config.yaml` si présent dans le répertoire de travail.
  Paramètres : `niveau`, `schema_couleur`, `methode`, `col_valeur`, `unite`.

- **Widgets Jupyter** — wrapping `ipywidgets` autour de `CarteCameroun` :
  `DropDown` pour le niveau et la zone_focus, `IntSlider` pour n_classes,
  bouton "Générer". Déclenche `visualiser()` à la volée dans le notebook.

- **Mode export presse** — variante de `carte_statique()` avec :
  cartouche bas de page (source, date, auteur), résolution 300 dpi forcée,
  fond blanc garanti, police plus grande pour lisibilité à l'impression.

---

## 12. Décisions de conception à ne pas remettre en question

**Pourquoi `adapter_dataset` est un module séparé et non des méthodes
dans `CarteCameroun` :** Il doit rester utilisable indépendamment de la
cartographie (nettoyage de données seul). Une dépendance inverse
(adapter_dataset → cameroun_viz) créerait un import circulaire.

**Pourquoi `focus()` et `drill_down()` retournent un nouvel objet plutôt
que de modifier `self` :** Permet de chaîner des appels sans perdre la
carte parente. Pattern immutable — l'objet source est toujours valide.
Ex: `carte.focus("Littoral").visualiser()` ne modifie pas `carte`.

**Pourquoi les valeurs manquantes sont représentées par des hachures grises
plutôt que par une couleur neutre :** Une couleur neutre (blanc, gris clair)
peut être confondue avec une valeur basse. Les hachures signalent visuellement
l'absence de donnée sans ambiguïté, y compris pour les daltoniens.

**Pourquoi Power BI passe par TopoJSON plutôt que par un visuel Python :**
Le visuel Python natif de Power BI n'est pas interactif (pas de tooltip
croisé avec les autres visuels, re-exécution à chaque interaction). Shape Map
+ TopoJSON donne une carte native, rapide, et intégrée au modèle de données.

**Pourquoi `normaliser_par_population()` n'est pas appelée automatiquement :**
Décision métier, pas technique. Diviser des cas de paludisme par la population
est raisonnable. Diviser un budget par la population peut ne pas l'être.
La librairie ne prend pas cette décision à la place de l'utilisateur.

---

## 13. Comportements à préserver lors des modifications

- `charger_metrique()` avec `auto_adapter=True` (défaut) doit fonctionner sur
  un DataFrame brut sans aucun paramètre supplémentaire si la structure est
  suffisamment claire. C'est le comportement "batteries included" central.

- `focus()` et `drill_down()` doivent continuer à retourner un objet
  `CarteCameroun` standard sur lequel toutes les méthodes fonctionnent.

- Le paramètre `afficher_contexte=True` dans `visualiser()` ne doit avoir
  d'effet que si `_zone_focus` est défini — sinon il est ignoré silencieusement.

- Les zones sans données doivent toujours apparaître en hachures grises,
  jamais être silencieusement exclues de la carte.

- `adapter_dataset()` ne doit jamais modifier le DataFrame d'entrée
  (`df.copy()` en début de fonction — invariant à maintenir).

---

## 14. Exemple minimal de test de non-régression

À exécuter après toute modification pour vérifier que rien n'a cassé :

```python
# Sans réseau, sans données réelles
import pandas as pd
import numpy as np
from shapely.geometry import Polygon
import geopandas as gpd
from cameroun_map import (
    CarteCameroun, joindre_metrique, classifier_valeurs,
    adapter_dataset, normaliser_nom, diagnostiquer_dataset,
)

# GeoDataFrame factice
gdf = gpd.GeoDataFrame({
    "NAME_1": ["Littoral", "Centre", "Est"],
    "geometry": [Polygon([(9,3),(11,3),(11,5),(9,5)]),
                 Polygon([(11,3),(13,3),(13,5),(11,5)]),
                 Polygon([(13,3),(15,3),(15,5),(13,5)])]
}, crs="EPSG:4326")

# Dataset avec nom mal orthographié et doublon
df = pd.DataFrame({
    "region": ["littoral", "littoral", "centre"],
    "valeur": [10.0, 12.0, 8.0],
})

# Test adapter_dataset
df_std = adapter_dataset(df, col_zone="region", col_valeur="valeur")
assert len(df_std) == 2, "Doublon non agrégé"
assert set(df_std["zone"]) == {"littoral", "centre"}

# Test normaliser_nom
assert normaliser_nom("littoral", ["Littoral", "Centre"]) == "Littoral"

# Test classifier_valeurs
import pandas as pd
bornes, etiq = classifier_valeurs(pd.Series([1,2,3,4,5]), "quantile", 3)
assert len(etiq) == 3

# Test joindre_metrique
gdf_joint = joindre_metrique(gdf, df_std,
    col_geo="NAME_1", col_metrique="zone", col_valeur="valeur")
assert "valeur" in gdf_joint.columns
assert "manquant" in gdf_joint.columns
assert gdf_joint.loc[gdf_joint["NAME_1"]=="Est", "manquant"].values[0] == True

print("Tous les tests passent.")
```

---

## 15. Environnement et compatibilité

- Python >= 3.10 (utilisation de `str | None` comme type hint)
- Testé sous Windows (environnement de l'utilisateur) et Linux (sandbox Claude)
- Sous Windows : installation de geopandas via conda recommandée
  (`conda install -c conda-forge geopandas`) à cause de GDAL/Fiona
- Encodage : tous les exports CSV utilisent `utf-8-sig` (avec BOM) pour que
  Excel et Power BI lisent correctement les accents sous Windows

---

## 16. Ce que cet utilisateur sait et attend

- Développeur/analyste Python, travaille sous Windows
- Utilise le projet en script Python et en notebook Jupyter
- A déjà testé et validé `test_rapide.py` avec succès
- Comprend les concepts de GeoDataFrame, jointure, choroplèthe
- N'a pas encore de vrais jeux de données — les tests sont faits avec
  des données simulées
- Priorité suivante : Phase 1 (tests, logging, cache, packaging)
- Contexte géographique : basé à Douala (Région Littoral, Cameroun)
