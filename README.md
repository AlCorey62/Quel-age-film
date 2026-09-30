# Quel âge pour ce film ?

Interface de recherche personnelle sur les fiches de [filmspourenfants.net](https://www.filmspourenfants.net) :
retrouver en deux secondes l'âge conseillé d'un film, d'une série ou d'un court-métrage, avec les scènes
difficiles, les thèmes et les messages relevés par le site.

- **Web app statique** (HTML/CSS/JS, sans framework ni build) — s'installe comme une appli sur le téléphone (PWA), fonctionne hors ligne.
- **Données toujours à jour** : un script Python interroge chaque nuit l'API REST publique WordPress du site
  (aucun scraping HTML) et ne télécharge que les fiches nouvelles ou modifiées.
- **Recherche instantanée** tolérante aux fautes, filtres par âge, format, univers, thèmes, technique, durée, année, pays.
- **Mes enfants** : prénoms et dates de naissance enregistrés sur l'appareil ; l'âge est recalculé automatiquement
  et chaque film affiche un verdict par enfant (OK / limite / pas encore).

## Mise en ligne en 5 minutes (GitHub Pages)

1. Crée un dépôt GitHub (privé ou public) et pousse ce dossier :
   ```bash
   git remote add origin git@github.com:<toi>/<depot>.git
   git push -u origin main
   ```
2. Dans le dépôt : **Settings → Pages → Build and deployment → Source : *Deploy from a branch*** ; branche `main`, dossier `/docs`. Enregistre.
3. Dans **Settings → Actions → General → Workflow permissions**, coche **Read and write permissions** (le robot doit pouvoir pousser les données).
4. Onglet **Actions → Synchronisation quotidienne → Run workflow** pour lancer une première synchro à la main (facultatif : les données du dépôt sont déjà à jour au moment du premier push).
5. L'appli est servie sur `https://alcorey62.github.io/Quel-age-film/`. Sur le téléphone : menu du navigateur → *Ajouter à l'écran d'accueil*.

Ensuite, tout est automatique : le workflow tourne chaque nuit à 04:17 UTC, commit les fiches modifiées, et GitHub Pages
republie le site. Sans changement côté source, aucun commit n'est créé.

## En local

```bash
python3 scripts/sync.py          # synchro incrémentale (première fois : ~10 min, ensuite quelques secondes)
python3 -m http.server -d docs   # puis http://localhost:8000
```

Options : `--full` retélécharge tout, `--limit N` limite à N fiches (test).

## Comment ça marche

```
scripts/sync.py          synchronisation (Python 3.10+, bibliothèque standard uniquement)
docs/                    site statique servi par GitHub Pages
  index.html / app.js / app.css / sw.js / manifest.webmanifest
  data/index.json        index léger de toutes les fiches (recherche et filtres côté navigateur)
  data/films/<id>.json   fiche complète (intro, messages, scènes difficiles, vocabulaire, distribution…)
  data/facets.json       valeurs et compteurs pour les filtres
  data/terms.json        libellés des taxonomies WordPress (cache)
  data/meta.json         date de dernière synchro, compteurs, pages ignorées
scripts/agemodel.py      estimation de l'âge (bibliothèque standard uniquement)
scripts/train_age_model.py, extract_descriptors.py, estimate_age.py, tmdb_source.py, evaluate_extractor.py, test_age.py
docs/model/              modèle entraîné (age_model.json), mesures (report.json), films de contrôle (parity.json)
docs/estimate.js         même calcul dans le navigateur
.github/workflows/sync.yml   planification quotidienne
.github/workflows/estimate.yml   estimations des sorties récentes
```

Le script :
1. balaie toutes les pages du site (identifiant + date de modification, 100 par requête) ;
2. compare avec l'index local pour trouver les fiches nouvelles, modifiées ou supprimées ;
3. télécharge uniquement celles-là, avec l'affiche, et complète les libellés de taxonomies manquants ;
4. découpe le texte de chaque fiche en sections (*Messages*, *Scènes difficiles*, *Vocabulaire*…) ;
5. réécrit les fichiers JSON. Une pause de 0,3 s sépare chaque requête pour ménager le site.

### Les âges

Le site attribue à chaque fiche deux âges et une plage :

| Donnée | Taxonomie WordPress | Dans l'appli |
|---|---|---|
| « À partir de X ans » | `gp_hubs` | âge conseillé (`af`), le grand chiffre |
| « Déconseillé aux moins de X ans » | `deconseille-aux-moins-de` | âge minimum (`am`) → verdict « limite » entre `am` et `af` |
| Âges auxquels le film convient | étiquettes `post_tag` | borne haute (`ax`) → « un peu bébé » au-delà |

## Estimer l'âge d'un film pas encore analysé

Le site met un peu de temps à analyser chaque nouveau film. L'appli peut donc estimer elle-même l'âge conseillé
dès la sortie, avec les mêmes critères que les 3 200 fiches existantes. Aucune classification officielle
(CNC, PEGI, MPAA, FSK…) n'est lue ni utilisée : la référence est uniquement l'analyse du site, plus prudente.

### Principe

1. **Décrire le film.** Claude lit le synopsis, les mots-clés et les avis de spectateurs, puis relève ce que relève
   le site : les 9 catégories de scènes difficiles (malaise, mises en danger, tristesse, visuel effrayant, moquerie,
   santé, banalisation de la violence, maltraitance, sexualité) avec leur intensité de 1 à 3, et les thèmes.
   Claude ne donne jamais l'âge lui-même.
2. **Convertir en âge.** Un modèle linéaire, entraîné sur les 3 200 fiches du site, additionne des contributions
   exprimées en années : format, durée, scènes, thèmes, studio, réalisation. Chaque estimation est donc explicable
   (« +1,1 an pour une scène de sexualité, +0,9 an pour de la maltraitance… »).
3. **Annoncer avec prudence.** L'appli affiche l'âge estimé, l'âge prudent et « déconseillé aux moins de ». Quand
   les informations sont maigres (confiance « faible »), l'âge annoncé est directement l'âge prudent.

Le même modèle tourne dans le navigateur (bouton **Estimer un film**) : tu coches toi-même les scènes que tu
connais et l'âge se met à jour en direct, sans réseau et sans clé.

### Précision mesurée

Mesures par validation croisée sur les 3 224 fiches : le modèle est testé sur des films qu'il n'a jamais vus.
Elles comparent l'estimation à l'âge que le site a réellement attribué.

| Ce que le modèle sait | Erreur moyenne | À ±1 an | À ±2 ans |
|---|---|---|---|
| Rien (toujours la valeur médiane) | 2,6 ans | | |
| Format, durée, année, studio, réalisation | 1,7 an | 54 % | 77 % |
| + description exacte des scènes et thèmes | 1,3 an | 67 % | 87 % |
| + erreurs d'extraction simulées (voir ci-dessous) | 1,4 an | 64 % | 85 % |
| Films de 2024 et après, modèle entraîné avant | 1,4 an | 66 % | 87 % |

**Âge prudent.** Sa marge dépend de l'âge estimé (de 0,35 an pour un film de 5 ans à 1,85 an autour de 11 ans),
car l'incertitude grandit avec l'âge. Il est au moins égal à l'âge du site dans **86 %** des cas, et dans 85 %
des cas à chaque tranche d'âge. Le prix de cette prudence : il surestime en moyenne de 1,3 an, et de 2 ans ou plus
dans 44 % des cas.

### Limites, à connaître avant de s'y fier

- **Il reproduit le jugement du site, il ne le remplace pas.** Une estimation dit ce que le site dirait
  probablement, pas une vérité indépendante. Pour un film qui inquiète, l'analyse du site ou un visionnage restent
  la référence.
- **Les films pour 13 ans et plus sont sous-estimés.** Quand le site attribue 13 ans ou plus, le modèle estime en
  moyenne 12,4 ans et l'âge prudent ne couvre que 63 % des cas (contre 89 % pour les films jusqu'à 12 ans). L'outil
  est fiable pour les enfants, pas pour trancher les films adolescents ou adultes.
- **La qualité de la lecture par Claude n'est pas mesurée.** Les lignes « erreurs d'extraction simulées » supposent
  que Claude retrouve 85 % des catégories de scènes présentes et en invente 4 % de fausses. C'est une hypothèse.
  Lance `python3 scripts/evaluate_extractor.py --n 60 --min-year 2025` (clés requises) pour la remplacer par une
  mesure réelle sur des films récents, puis relance l'entraînement si les marges doivent changer.
- **Les sorties toutes récentes ont peu d'informations.** Sans avis de spectateurs, Claude ne voit que le synopsis
  et rate des scènes : l'âge est alors sous-estimé, d'où la règle de l'âge prudent pour la confiance « faible ».
  Les films de 2024 et après sont déjà un peu sous-estimés (0,3 an en moyenne).
- **L'écart avec les classifications officielles n'est pas chiffré**, puisqu'elles ne sont pas dans les données.
  La sévérité vient uniquement du fait que le modèle reproduit les âges du site.

### Utilisation

```bash
pip install -r scripts/requirements-ml.txt

python3 scripts/train_age_model.py                 # réentraîne (12 s) et réécrit docs/model/
python3 scripts/test_age.py                        # 13 tests, sans réseau

python3 scripts/estimate_age.py --descriptors film.json          # à la main, sans réseau
python3 scripts/estimate_age.py --title "Titre" --year 2026 --runtime 95 --synopsis "…"   # Claude lit
python3 scripts/estimate_age.py --tmdb 123456                    # depuis TMDB
python3 scripts/estimate_age.py --new-releases 21                # lot → docs/data/estimates.json
```

Le format de `film.json` est décrit en tête de `scripts/agemodel.py`. Les modes avec Claude demandent
`ANTHROPIC_API_KEY`. Le modèle par défaut est `claude-opus-5-5` (variable `AGE_LLM_MODEL` pour en changer) ; le
coût est de l'ordre de quelques centimes par film (estimation, non mesurée).

### Publication automatique des sorties

Le workflow `estimate.yml` s'exécute après chaque synchro quotidienne : il cherche les films d'animation et
familiaux sortis ces 21 derniers jours sur TMDB, estime ceux que le site n'a pas encore, publie le résultat dans
`docs/data/estimates.json` et retire les estimations dont la fiche du site est parue. Elles apparaissent dans la
liste avec la pastille « Estimation automatique » ; le filtre d'âge s'appuie sur l'âge prudent.
Une fois par semaine, il réentraîne aussi le modèle sur les fiches les plus récentes.

Pour l'activer, crée un compte gratuit sur themoviedb.org (Réglages, API, « API Read Access Token »), puis ajoute
deux secrets au dépôt (Settings, Secrets and variables, Actions) : `ANTHROPIC_API_KEY` et `TMDB_TOKEN`. Sans eux,
le workflow se termine sans rien faire.

## Pour l'équipe de filmspourenfants.net

Cette section répond aux questions habituelles sur la façon dont le projet interroge le site.

**Aucun scraping HTML.** Le script n'utilise que l'API REST standard de WordPress, publique et
déjà exposée par le site (`/wp-json/wp/v2/…`). Les pages HTML ne sont jamais téléchargées.

**Points d'accès utilisés**

| Appel | Rôle | Fréquence |
|---|---|---|
| `GET /wp/v2/pages?_fields=id,modified_gmt&per_page=100&page=N` | balayage : identifiants et dates de modification | 33 requêtes par nuit |
| `GET /wp/v2/pages?include=…&_embed=wp:featuredmedia` | fiches nouvelles ou modifiées uniquement, par lots de 50 | 0 à 2 requêtes par nuit en régime normal |
| `GET /wp/v2/<taxonomie>?include=…` | libellés de termes encore inconnus (nouveau thème, nouveau studio…) | rarement |

Soit environ **35 requêtes par nuit**, espacées de 0,3 s, à 04:17 UTC. La toute première
synchronisation, faite une seule fois, a représenté environ 200 requêtes sur 13 minutes.
Les affiches ne sont pas copiées : l'interface charge les images depuis vos URL d'origine
(CDN Jetpack `i0.wp.com`).

**Identification.** Chaque requête porte le User-Agent
`filmspourenfants-sync/1.0 (usage personnel; synchro quotidienne via API REST)`, ce qui permet
de la reconnaître dans vos journaux et, si besoin, de la bloquer côté serveur.

**Ce qui est stocké.** Titre, lien vers la fiche, âges, taxonomies (format, année, durée, studio,
pays, créateurs, acteurs, univers, technique, thèmes) et le texte de la fiche découpé en sections
(intro, Messages, Scènes difficiles, Vocabulaire). Rien d'autre : ni commentaires, ni utilisateurs,
ni contenu non publié.

**Réutilisation.** Le dossier `docs/` est un site statique autonome, sans framework ni étape de
construction. Il peut être servi tel quel depuis n'importe quel hébergement, ou intégré à une refonte :
- `docs/data/` est le seul couplage avec la source ; il peut être produit par `scripts/sync.py`
  comme aujourd'hui, ou directement par WordPress (un export JSON à la publication d'une fiche
  suffit) ;
- `docs/app.js` contient toute la logique de recherche et de filtrage côté navigateur, sur un index
  de 2,3 Mo pour 3 200 fiches, ce qui reste instantané sur mobile ;
- le code est sous licence MIT (voir `LICENSE`) : libre d'usage, de modification et d'intégration.

## Remarques

- Les textes et affiches restent la propriété de filmspourenfants.net ; chaque fiche renvoie vers la page d'origine. Le code, lui, est sous licence MIT.
- Si le site change de structure (rare : c'est l'API standard de WordPress), le workflow échoue et GitHub envoie un e-mail ; les données déjà publiées continuent de fonctionner.
