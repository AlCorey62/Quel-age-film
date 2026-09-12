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
.github/workflows/sync.yml   planification quotidienne
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
