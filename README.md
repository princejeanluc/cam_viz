# Cameroun Map Viz

Visualisation cartographique du Cameroun par métrique, à n'importe quel niveau administratif (régions, départements, arrondissements, quartiers), à partir d'un fichier de données quelconque (CSV, Excel exporté, format large ou long, avec ou sans doublons).

Le projet résout deux problèmes distincts :

1. **La géométrie** — où sont les frontières des régions/départements/arrondissements du Cameroun, et comment les afficher en carte statique ou interactive.
2. **La donnée** — comment transformer un fichier réel, mal structuré, en quelque chose qui peut être joint à cette géométrie sans erreur silencieuse.

## Sommaire

- [Démarrage rapide](#démarrage-rapide)
- [Installation](#installation)
- [Structure du projet](#structure-du-projet)
- [Concepts clés](#concepts-clés)
- [Guide d'utilisation](#guide-dutilisation)
- [Format de données attendu](#format-de-données-attendu)
- [Niveaux administratifs](#niveaux-administratifs)
- [Choix de visualisation](#choix-de-visualisation)
- [Dépannage](#dépannage)
- [Limites connues](#limites-connues)

## Démarrage rapide

```bash
pip install -e .
python tests/test_rapide.py
```

Ce script crée son propre jeu de données factice, télécharge la géométrie des régions du Cameroun, et génère une carte PNG (`test_carte.png`). S'il s'exécute sans erreur, l'installation est correcte.

Pour une découverte guidée du projet, ouvrez [`examples/galerie.ipynb`](examples/galerie.ipynb) dans Jupyter — il reprend chaque étape (carte minimale, validation de la jointure, comparaison de méthodes de classification, zoom, drill-down, nettoyage de données brutes) avec des explications entre les cellules.

Pour vos propres données :

```python
import pandas as pd
from cameroun_map import CarteCameroun

df = pd.read_csv("mon_fichier.csv")

carte = CarteCameroun(niveau="regions")
carte.charger_geo()
carte.charger_metrique(df)          # diagnostic + adaptation automatique
carte.visualiser(
    titre="Mon indicateur — Cameroun",
    unite="%",
    sortie="ma_carte.png"
)
```

## Installation

```bash
pip install -e .
# avec fuzzy matching + export Power BI
pip install -e ".[fuzzy,powerbi]"
```

Dépendances principales : `geopandas`, `pandas`, `matplotlib`, `folium`, `plotly`, `mapclassify`.
Extras optionnels : `fuzzy` (`thefuzz`, `python-Levenshtein`), `powerbi` (`topojson`), `osm` (`osmnx`), `dev` (`pytest`).

Sous Windows, si l'installation de `geopandas` échoue à cause de dépendances système (GDAL, Fiona), passez par conda :

```bash
conda install -c conda-forge geopandas
pip install -e .
```

Le premier chargement géographique télécharge les frontières administratives depuis [GADM](https://gadm.org) — une connexion internet est nécessaire la première fois. Les fichiers sont ensuite mis en cache dans un dossier `data/` local, donc les exécutions suivantes fonctionnent hors ligne.

## Structure du projet

```
camviz/
├── pyproject.toml
├── requirements.txt
├── src/
│   └── cameroun_map/
│       ├── __init__.py        → API publique du package
│       ├── cameroun_viz.py    → moteur principal : géométrie, jointure, rendu
│       ├── adapter_dataset.py → nettoyage et normalisation des données brutes
│       └── export_powerbi.py  → prépare TopoJSON + CSV pour Power BI (Shape Map)
├── examples/
│   ├── galerie.ipynb            → notebook pédagogique, du CSV à la carte vérifiée
│   └── exemples_utilisation.py  → cas d'usage commentés (régions, quartiers, multicouches...)
├── tests/
│   └── test_rapide.py          → script de vérification d'installation, autonome
└── data/                       → cache local des géométries GADM (créé au premier lancement)
```

Deux modules à connaître pour commencer :

- `cameroun_map.cameroun_viz` contient la classe `CarteCameroun`, l'interface principale du projet.
- `cameroun_map.adapter_dataset` contient les fonctions qui transforment un dataset brut en quelque chose d'exploitable. Il peut aussi être utilisé seul, indépendamment de la cartographie, simplement pour nettoyer un fichier.

Tout est exposé directement depuis `cameroun_map` (`from cameroun_map import CarteCameroun, adapter_dataset, ...`) une fois le package installé avec `pip install -e .`.

## Concepts clés

### Le pipeline en trois étapes

Toute carte produite par ce projet suit la même séquence :

```
géométrie (GADM/OSM)  +  données métriques  →  jointure  →  rendu
```

`charger_geo()` télécharge ou charge depuis le cache les contours administratifs. `charger_metrique()` diagnostique votre fichier, le nettoie si besoin, puis le fusionne aux contours par nom de zone. `visualiser()` ou `visualiser_interactif()` produit l'image ou la carte HTML finale.

Une jointure réussie n'est pas la même chose qu'une jointure correcte : le fuzzy matching peut associer un nom à la mauvaise zone sans qu'aucune erreur ne soit levée. `carte.valider()` (voir [Vérifier la jointure avant de publier](#vérifier-la-jointure-avant-de-publier-une-carte)) sert précisément à distinguer les deux avant de faire confiance au rendu.

### Pourquoi une étape d'adaptation séparée

Le code a d'abord été écrit en supposant un format de données idéal (`colonne nom de zone` + `colonne valeur`, une ligne par zone). Ce format n'existe presque jamais dans la réalité : exports Excel avec une colonne par mois, noms de région mal orthographiés, doublons de saisie, valeurs brutes au lieu de taux normalisés. `adapter_dataset.py` a été ajouté pour absorber cette variabilité avant que la jointure géographique n'échoue silencieusement (zones vides sur la carte sans message d'erreur explicite).

## Guide d'utilisation

### Cas 1 — Carte simple par région

```python
from cameroun_map import CarteCameroun
import pandas as pd

df = pd.read_csv("taux_pauvrete.csv")

carte = CarteCameroun(niveau="regions")
carte.charger_geo()
carte.charger_metrique(df)
carte.diagnostiquer()              # affiche un résumé de la jointure
carte.valider()                    # table nom CSV ↔ zone géo, avec score de correspondance

carte.visualiser(
    titre="Taux de pauvreté par région — Cameroun 2024",
    unite="%",
    methode="jenks",                # ou 'quantile', 'égale', 'std'
    schema_couleur="OrRd",
    sortie="carte_pauvrete.png"
)
```

### Vérifier la jointure avant de publier une carte

`carte.diagnostiquer()` dit combien de zones ont des données. `carte.valider()` dit *comment* chaque zone a obtenu sa donnée — c'est la différence entre une carte qui s'affiche sans erreur et une carte dont on peut garantir l'exactitude.

```python
carte.valider(seuil_alerte=85)
```

Affiche, pour chaque zone géographique : le nom tel qu'il apparaît dans votre CSV (`nom_dans_csv`), la méthode de correspondance (`exact` ou `fuzzy`), le score (0-100), la valeur jointe, et si la zone est restée sans donnée. Deux choses à surveiller dans la sortie :

- **Une ligne `[ALERTE]` sous le seuil** — une correspondance fuzzy a réussi (score ≥ 70 par défaut) mais reste incertaine (< `seuil_alerte`, 85 par défaut). Exemple : `"Nord" → "Nord-Ouest" (72%)` mérite une vérification manuelle même si la jointure n'a pas échoué.
- **Un message `[join][ALERTE]` au moment de `charger_metrique()`** — deux noms différents de votre CSV se sont résolus vers la même zone géographique (collision). Les valeurs ont été moyennées automatiquement ; si ce n'est pas le comportement voulu, corrigez l'orthographe dans le CSV source plutôt que de laisser la moyenne silencieuse.

`valider()` retourne aussi le DataFrame de correspondance, exportable avec `.to_csv()` pour le partager ou l'archiver comme preuve de la jointure utilisée.

### Cas 2 — Carte interactive (HTML)

```python
carte.visualiser_interactif(
    titre="Taux de pauvreté — Cameroun",
    moteur="folium",                # ou 'plotly'
    unite="%",
    sortie="carte_pauvrete.html"
)
```

Ouvrez le fichier `.html` produit dans un navigateur. Il fonctionne hors ligne et peut être partagé tel quel.

### Cas 3 — Niveau plus fin (départements, arrondissements)

```python
carte = CarteCameroun(niveau="departements")   # ou "arrondissements"
carte.charger_geo()
carte.charger_metrique(df)
```

### Cas 4 — Quartiers d'une ville (Douala, Yaoundé)

```python
carte = CarteCameroun(niveau="osm_quartiers")
carte.charger_geo(ville_osm="Douala")
carte.charger_metrique(df)
```

Ce niveau dépend des données disponibles sur OpenStreetMap, qui sont incomplètes ou irrégulières selon les quartiers — voir [Limites connues](#limites-connues).

### Cas 5 — Zoom sur une zone : filtre spatial (mode A)

Recadre la vue sur une zone précise sans changer de niveau administratif. Le reste du Cameroun reste visible en fond grisé pour donner le contexte géographique.

```python
carte = CarteCameroun(niveau="regions")
carte.charger_geo()
carte.charger_metrique(df_regions)

# Affiche uniquement la région Littoral, fond national grisé en arrière-plan
zoom = carte.focus("Littoral")
zoom.visualiser(
    titre="Région Littoral — accès à l'eau",
    afficher_contexte=True,   # fond grisé = contexte national (True par défaut)
    sortie="littoral_focus.png"
)
```

### Cas 6 — Drill-down : subdivisions internes (mode B)

Change de niveau administratif en se restreignant à une zone parente. Par exemple, afficher les 4 départements du Littoral à partir d'une carte régionale.

```python
carte = CarteCameroun(niveau="regions")
carte.charger_geo()
carte.charger_metrique(df_regions)

zoom = carte.drill_down(
    zone="Littoral",
    niveau_interieur="departements",
    df_metrique=df_departements,   # données niveau département (optionnel)
)
zoom.visualiser(
    titre="Départements du Littoral — couverture vaccinale",
    afficher_contexte=True,
    sortie="littoral_departements.png"
)
```

Les deux modes sont combinables avec `visualiser_interactif()`. Le fond grisé (contexte national) est contrôlé par `afficher_contexte=True/False`.

### Cas 7 — Diagnostiquer un fichier sans faire de carte

Utile pour comprendre un fichier avant de l'utiliser, ou pour nettoyer une donnée indépendamment de tout objectif cartographique.

```python
from cameroun_map import diagnostiquer_dataset, adapter_dataset

diagnostiquer_dataset(df)           # rapport texte, ne modifie rien
df_propre = adapter_dataset(df)     # renvoie un DataFrame standardisé
```

Plus d'exemples commentés dans `exemples_utilisation.py` (multicouches avec points superposés, comparaison des méthodes de classification côte à côte).

## Format de données attendu

`charger_metrique()` accepte un DataFrame dans à peu près n'importe quelle forme raisonnable. En interne, `adapter_dataset()` essaie de le ramener à ce format minimal :

| zone | valeur | temps (optionnel) |
|---|---|---|
| Littoral | 12.3 | 2024 |
| Centre | 8.1 | 2024 |

Ce que la détection automatique gère sans intervention :

- **Format large → long** : une colonne par année, par mois (`Janvier 2024`), ou par trimestre (`T1 2023`) est automatiquement convertie en une ligne par période.
- **Doublons** : si une même zone apparaît plusieurs fois, les valeurs sont agrégées (moyenne par défaut, configurable en somme/max/min).
- **Détection de colonnes** : si une seule colonne texte ressemble à un nom de zone et une seule colonne numérique existe, elles sont identifiées automatiquement.

Ce que la détection automatique ne fait **pas**, volontairement, car la décision est métier et non technique :

- **Granularité mixte** : si le fichier mélange des lignes région et des lignes département, utilisez `separer_par_niveau()` pour les séparer avant de cartographier chaque niveau séparément.
- **Normalisation par habitant** : une valeur brute (nombre de cas) n'est pas convertie automatiquement en taux. Utilisez `normaliser_par_population(df, "cas", "population", pour=100_000)` si la comparaison entre zones de tailles différentes doit être équitable.

Si la détection se trompe ou échoue, précisez explicitement les colonnes :

```python
carte.charger_metrique(df, col_nom="ma_colonne_region", col_valeur="mon_indicateur")
```

## Niveaux administratifs

| Niveau | Argument `niveau=` | Nombre de zones | Source |
|---|---|---|---|
| Pays | `"pays"` | 1 | GADM |
| Régions | `"regions"` | 10 | GADM |
| Départements | `"departements"` | 58 | GADM |
| Arrondissements | `"arrondissements"` | ~360 | GADM |
| Quartiers (ville) | `"osm_quartiers"` | variable | OpenStreetMap |

Plus le niveau est fin, plus la fiabilité de la géométrie et la disponibilité de données statistiques officielles correspondantes diminuent. Les régions sont le niveau le plus robuste pour une première carte ; les quartiers nécessitent une vérification visuelle systématique (voir Limites connues).

## Choix de visualisation

| Besoin | Fonction | Sortie |
|---|---|---|
| Image pour un rapport, une publication | `visualiser()` | PNG/SVG haute résolution |
| Carte explorable, tooltip au survol | `visualiser_interactif(moteur="folium")` | HTML autonome |
| Intégration dans Streamlit/Dash | `visualiser_interactif(moteur="plotly")` | objet Plotly |
| Recadrer sur une zone, même niveau | `carte.focus("Littoral")` | nouvel objet `CarteCameroun` |
| Subdivisions internes d'une zone | `carte.drill_down("Littoral", "departements")` | nouvel objet `CarteCameroun` |

Pour la classification des valeurs en classes de couleur, quatre méthodes sont disponibles via le paramètre `methode` : `quantile` (effectifs égaux, recommandé par défaut), `jenks` (ruptures naturelles, utile sur données hétérogènes), `égale` (intervalles de même largeur), `std` (basé sur l'écart-type). `exemples_utilisation.py` contient une fonction qui les compare visuellement côte à côte sur le même jeu de données.

## Utilisation dans Power BI

Power BI n'exécute pas ce projet directement — il faut le distinguer de ce que les visuels Power BI savent faire nativement. Le chemin recommandé passe par le visuel intégré **Shape Map**, qui accepte un fichier **TopoJSON** personnalisé. Le rôle du projet Python change alors : il ne dessine plus la carte, il prépare les deux fichiers dont Power BI a besoin.

```python
from cameroun_map import exporter_pour_powerbi
import pandas as pd

df = pd.read_csv("mon_indicateur.csv")

exporter_pour_powerbi(
    niveau="regions",            # ou "departements", "arrondissements"
    df_metrique=df,
    dossier_sortie="export_powerbi"
)
```

Cela génère dans `export_powerbi/` :

- `cameroun_regions.topojson` — les contours, à importer dans Power BI Desktop via Format > Forme de carte > Modifier > + Ajouter une carte
- `donnees_powerbi.csv` — vos données avec une colonne `zone` dont les noms correspondent **exactement** aux formes du TopoJSON (Power BI ne fait pas de correction approximative des noms, contrairement au reste de ce projet)
- `INSTRUCTIONS_POWERBI.txt` — les étapes détaillées d'import dans Power BI Desktop

Pour préparer une carte centrée sur une seule région (équivalent du `focus()`/`drill_down()` du pipeline Python) :

```python
exporter_pour_powerbi(
    niveau="departements",
    zone_focus="Littoral",        # ne garde que les départements du Littoral
    dossier_sortie="export_powerbi_littoral"
)
```

Le drill-down interactif au clic (région → départements dans le même visuel) n'est pas nativement supporté par Shape Map ; l'approche courante consiste à créer deux pages de rapport reliées par un signet, comme détaillé dans `INSTRUCTIONS_POWERBI.txt`.

**Pourquoi pas un visuel Python directement dans Power BI ?** C'est possible (`Python visual` natif), mais le script s'exécute à chaque interaction, ne propose aucun tooltip ni filtre croisé avec les autres visuels du rapport, et reste lent. Le Shape Map alimenté par TopoJSON est le seul chemin qui donne une carte réellement interactive dans Power BI.

## Dépannage

**`FileNotFoundError` sur un CSV** — le chemin du fichier dans votre script ne correspond pas à un fichier réel sur le disque. Vérifiez le chemin avec `import os; os.path.exists("mon_fichier.csv")`.

**Des zones restent grises avec hachures sur la carte** — la jointure n'a pas trouvé de correspondance pour ces zones. `carte.diagnostiquer()` liste les zones sans données. Vérifiez l'orthographe exacte dans votre fichier source, ou passez par `adapter_dataset.diagnostiquer_dataset(df)` pour voir comment vos noms de zone sont interprétés.

**La carte s'affiche, toutes les zones ont une valeur, mais un chiffre semble faux** — un échec de jointure se voit (zone grise). Une mauvaise jointure réussie ne se voit pas : le fuzzy matching peut avoir associé votre donnée à la mauvaise zone sans déclencher d'erreur. Lancez `carte.valider()` et vérifiez les lignes `methode=fuzzy` avec un score faible, ainsi que les messages `[join][ALERTE]` affichés au moment de `charger_metrique()` (collision : deux noms d'origine différents fusionnés vers la même zone).

**Échec du téléchargement GADM (`ConnectionResetError`, `ConnectionError` pendant le handshake TLS)** — `charger_gadm()` retente automatiquement 4 fois avec un délai croissant avant d'abandonner ; ces erreurs sont souvent transitoires (antivirus qui inspecte le HTTPS, réseau d'entreprise, ou serveur GADM temporairement instable) et un nouvel essai (`carte.charger_geo()` à nouveau) suffit parfois. Si l'échec persiste, téléchargez manuellement le GeoJSON depuis [gadm.org/download_country.html](https://gadm.org/download_country.html) (choisir Cameroon) et placez le fichier dans `data/cameroun_gadm_niveau{N}.geojson` — le chemin exact attendu est affiché dans le message d'erreur.

**`thefuzz` non installé** — le matching approximatif des noms de zone est désactivé sans erreur bloquante, mais les noms mal orthographiés (`"extreme nord"` au lieu de `"Extrême-Nord"`) ne seront plus corrigés automatiquement. Installez avec `pip install thefuzz python-Levenshtein`.

**Erreur d'installation de `geopandas` sous Windows** — voir la section [Installation](#installation), privilégier conda.

## Limites connues

- Les données GADM datent d'une mise à jour qui peut ne pas refléter les derniers découpages administratifs officiels du Cameroun.
- Le niveau quartier dépend entièrement de ce qui a été cartographié sur OpenStreetMap, ce qui est très inégal entre Douala/Yaoundé et le reste du pays — certains quartiers peuvent être absents ou mal délimités.
- La détection automatique de format (`adapter_dataset`) couvre les cas les plus fréquents observés, mais reste heuristique : toujours vérifier le rapport de `diagnostiquer_dataset()` avant de faire confiance à une carte produite sur un nouveau fichier.
- `carte.valider()` réduit le risque d'une jointure fuzzy incorrecte mais ne l'élimine pas : un nom mal orthographié peut toujours matcher fortement (score élevé) vers la mauvaise zone si les deux noms sont très proches orthographiquement. Le score est une aide à la décision, pas une garantie.
- Aucune validation officielle des frontières n'est effectuée ; ce projet est un outil de visualisation, pas une source géographique de référence.
