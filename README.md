<p align="center"><img src="logo.png" alt="Sentinel" width="140"></p>

# Sentinel

Surveillance de la maison pour Home Assistant : tu modélises ta maison en 3D
(étages, pièces, ouvertures, meubles, extérieur), tu y places tes capteurs, et
tu définis **tes propres modes** en choisissant, pour chaque entité, le niveau
qu'elle prend quand elle se déclenche : `off`, `info`, `warning` ou `critical`.

> Version **0.1.0**, première version de test. Voir [Limites](#limites-de-la-v0.1).

## Ce que fait Sentinel

- **Modèle 3D** de la maison dans un panneau de la barre latérale, avec l'état en direct de chaque capteur.
- **Modes libres** : crée, renomme, duplique tes modes (Désarmé, Présence, Absence, Nuit, Vacances...).
- **Niveau par entité, zone ou type** : priorité entité > zone > type > défaut.
- **Notifications par niveau et par mode**, ou propres à une zone : service `notify.*`, message avec variables, rappel périodique, notification à l'activation du mode et au retour à la normale.
- **Focus** (onglet Surveillance) : met en avant ce qui est surveillé dans le mode actif (teinte légère), le reste passe en fil de fer ; filtres par niveau et par type d'entité.
- **Ondes de détection** (maquette 3D) : un élément déclenché émet des ondes concentriques au sol (capteurs) ou une sphère sonar (ouvertures). Plus le niveau monte, plus elles sont nombreuses et rapides ; un déclenchement en attente (délai d'entrée ou de sortie) donne une seule onde lente et ambre.
- **Zones occupées** : un détecteur de mouvement garde sa zone occupée ; un mouvement dans une zone voisine la désactive après un délai réglable par mode (« Zone quittée »), même si le détecteur reste « on ».
- **Actions** : à chaque évènement d'un mode (mode activé, info, warning, critical, retour à la normale), appelle des services Home Assistant (lancer un script, allumer ou éteindre une entité…), au niveau du mode ou propres à une zone.
- **Délais** : temporisation par entité, délai d'entrée et de sortie par mode.
- **Entités Home Assistant** pour tes automatisations (statut, compteurs, mode, alarme, zones).

## Installation

### Avec HACS (dépôt personnalisé)

1. HACS > Intégrations > menu ⋮ > *Dépôts personnalisés*.
2. Ajoute `https://github.com/ZephyrOff/Sentinel`, catégorie *Intégration*.
3. Installe **Sentinel**, puis redémarre Home Assistant.

### À la main

1. Copie le dossier `custom_components/sentinel` dans `/config/custom_components/`.
2. Redémarre Home Assistant.

### Activation

*Paramètres > Appareils et services > Ajouter une intégration > Sentinel.*
Un élément **Sentinel** apparaît dans la barre latérale (réservé aux administrateurs).

## Premiers pas

1. Ouvre le panneau **Sentinel**, onglet **Export**, et clique sur *Charger la maison d'exemple* pour voir à quoi ressemble une configuration complète (aucune entité réelle n'y est liée), ou pars de zéro.
2. Onglet **Modélisation** : ajoute un étage, des pièces (« + Pièce », « + Zone extérieure »), des ouvertures et des meubles. Glisse-les sur le plan, règle les dimensions dans l'inspecteur. Une pièce sélectionnée affiche ses sommets : glisse-les pour changer la forme, tire le point au milieu d'un mur pour le décrocher, double-clique un sommet pour le supprimer, ou pars d'un gabarit (L, T, pan coupé).
3. Pour chaque capteur, renseigne **l'entité Home Assistant** dans l'inspecteur (liste d'autocomplétion) :
   - `binary_sensor.*` (contact, mouvement, présence, fumée, fuite),
   - ou `cover.*`, `lock.*`, `switch.*`, `input_boolean.*`.
   L'état `on`, `open`, `opening`, `unlocked`, `detected`, `triggered`, `wet` compte comme déclenché. Coche *Inverser* pour les capteurs à logique inverse.
4. Onglet **Modes** : choisis le niveau de chaque entité par mode, règle les notifications (service `notify.mobile_app_...`) et les actions. Le sélecteur « Portée » bascule entre le mode entier et une zone : une zone peut avoir ses propres notifications et actions, qui remplacent celles du mode ou s'y ajoutent (case à cocher), le reste suit le mode. L'encart « Aide » liste les variables `{name}`, `{zone}`, `{floor}`, `{type}`, `{state}`, `{level}`, `{mode}`, `{entity}`, `{time}`, `{date}` ; un clic les insère dans le champ en cours.
5. En haut, sélectionne le mode actif. Tout est sauvegardé automatiquement dans Home Assistant.

## Entités créées

Le préfixe (`sentinel` par défaut) et chaque identifiant se règlent dans l'onglet **Entités HA**.

| Entité | Valeur |
|---|---|
| `select.sentinel_mode` | Nom du mode actif (modifiable) |
| `sensor.sentinel_status` | `ok`, `info`, `warning` ou `critical` (pire niveau déclenché) |
| `sensor.sentinel_trigger_counter` | Nombre d'entités déclenchées (attributs `info`, `warning`, `critical`, `total_since_reset`) |
| `sensor.sentinel_critical_count` / `_warning_count` / `_info_count` | Nombre d'entités par niveau |
| `binary_sensor.sentinel_alarm` | `on` dès qu'une entité est en `critical` |
| `sensor.sentinel_last_trigger` | Nom de la dernière entité déclenchée (attributs `level`, `zone`, `time`) |
| `sensor.sentinel_zone_<zone>_status` | Statut de chaque zone |

## Services et événements

| Service | Rôle |
|---|---|
| `sentinel.set_mode` | `mode`: nom ou identifiant du mode |
| `sentinel.acknowledge` | Coupe les rappels et efface les déclenchements mémorisés. Avec `entity_id`, suspend la détection de cette entité pendant la « Pause après acquittement » du mode (60 s par défaut), puis elle reprend |
| `sentinel.reset_counters` | Remet à zéro le cumul et le dernier déclenchement |

Événements : `sentinel_triggered` (`entity_id`, `sentinel_id`, `name`, `level`, `zone`, `mode`) et `sentinel_cleared`.

Exemple d'automatisation :

```yaml
automation:
  - alias: Sentinel – alerte critical
    trigger:
      - platform: state
        entity_id: sensor.sentinel_status
        to: "critical"
    action:
      - service: notify.mobile_app_alex
        data:
          message: >
            {{ states('sensor.sentinel_trigger_counter') }} entité(s) déclenchée(s),
            dernier : {{ states('sensor.sentinel_last_trigger') }}
```

## Comportement des délais

- **Temporisation (entité)** : l'entité doit rester déclenchée ce nombre de secondes avant de compter.
- **Délai d'entrée (mode)** : ajouté à la temporisation, uniquement pour le niveau `critical` et jamais pour la fumée et les fuites.
- **Délai de sortie (mode)** : après un changement de mode, rien ne se déclenche pendant ce délai. Ce qui est déjà ouvert est réévalué ensuite.
- **Rappel** : un niveau avec « Répéter » renvoie la notification tant que l'entité reste déclenchée, sauf acquittement.

## Limites de la v0.1

- Les entités exposées ne sont pas renommées si tu changes le préfixe après leur création : renomme-les dans le registre d'entités de Home Assistant.
- Les pièces sont des polygones à angles quelconques (L, T, pan coupé…) sans trou : pas de cour intérieure.
- Les règles se basent sur l'état courant : pas encore de plages horaires ni de bascule automatique de mode.
- Pas d'actions sortantes (sirène, lumières) : utilise les événements et le statut dans tes automatisations.
- Le panneau utilise des modules WebGL : un navigateur récent est nécessaire.

## Cartes Lovelace (style iOS)

Les cartes sont chargées automatiquement avec l'intégration (aucune ressource à ajouter) et reprennent les
variables du thème Home Assistant : elles s'accordent au thème iOS clair ou sombre. Les entités Sentinel
sont retrouvées toutes seules.

| Carte | Contenu |
|---|---|
| `custom:sentinel-status-card` | Mode, état global, délai de sortie ; `show_counts: true` ajoute les compteurs, `show_modes: true` les petits boutons de mode |
| `custom:sentinel-control-card` | Statut avec petits boutons pour activer les modes (statut + `show_modes`) |
| `custom:sentinel-mode-card` | Changer de mode ; ouvre le digicode si le mode exige un code |
| `custom:sentinel-keypad-card` | Digicode d'armement / désarmement (`target`, `modes` en option) |
| `custom:sentinel-zones-card` | Zones et niveau de chacune (`floor`, `entities` en option) |
| `custom:sentinel-ack-card` | Éléments déclenchés ou en détection, acquittement par ligne ou global |
| `custom:sentinel-chips-card` | Chips `chips: [mode, status, zones, ack]` |
| `custom:sentinel-plan-card` | Plan 2D (par étage) et maquette 3D (`default_view: 2d` ou `3d`) |
| `custom:sentinel-journal-card` | Derniers évènements (`max`) |

**Sécurité du changement de mode** (*Modes › Réglages › Sécurité*), mode par mode :

- **Code** : chaque mode peut avoir son propre code (4 à 12 chiffres, stocké haché, vérifié par Home Assistant,
  jamais dans le navigateur). Un code par défaut (*Entités HA*) sert aux modes qui n'en ont pas. Le service
  `sentinel.set_mode` accepte le paramètre `code` ; cinq essais incorrects verrouillent 60 s.
- **Validation sur un appareil de confiance** : le changement n'est appliqué que lorsque vous appuyez sur
  « Valider » dans la notification envoyée au service choisi (par exemple `notify.mobile_app_iphone`,
  application mobile Home Assistant). Délai réglable. Avec un code, le code est vérifié d'abord.
- Les deux peuvent être combinés. Le panneau et les cartes suivent le même parcours : fenêtre de code, puis
  notification. Un refus ou une expiration s'affiche sur les cartes et dans le panneau.

La carte plan accepte `view: 2d` ou `view: 3d` pour figer l'affichage (sans `view`, un sélecteur 2D / 3D est proposé).

## Licence

[MIT](LICENSE)


## Développement

Logique pure et moteur testés sans Home Assistant :

```bash
python tests/test_logic.py
python tests/test_engine.py
```

Le panneau fonctionne aussi seul : ouvre `custom_components/sentinel/frontend/index.html` dans un navigateur pour obtenir la démo (données d'exemple, simulation par clic).

```
custom_components/sentinel/
  __init__.py         enregistrement du panneau, des services, des plateformes
  engine.py           moteur : états, délais, notifications, événements
  logic.py            logique pure (niveaux, zones, modèle de configuration)
  websocket_api.py    API du panneau (config, sauvegarde, flux d'état)
  select.py sensor.py binary_sensor.py
  frontend/           panneau (index.html + sentinel-panel.js + three.js)
```
