# PID Toolbox — Synthèse de la série « Fundamentals » (Brian)

Notes consolidées des sessions Q&A de Brian (PID Toolbox).

**Sources :** Q&A #21 (PID Fundamentals 1), #22 (PID Fundamentals 2), #23 (PID Fundamentals 3), #16 (Filter Tuning for Performance), QuickTips (clean step response).

---

## Sommaire

1. [Comment chaque terme se calcule](#1-comment-chaque-terme-se-calcule)
2. [Sliders et ordre des opérations](#2-sliders-et-ordre-des-opérations)
3. [Lire la step response](#3-lire-la-step-response)
4. [Filtrage pour la performance](#4-filtrage-pour-la-performance)
5. [Récapitulatif & pièges récurrents](#5-récapitulatif--pièges-récurrents)

---

## 1. Comment chaque terme se calcule
*Source : Q&A #21 — PID Fundamentals 1*

Pour voir les PID travailler, on fait un **wobble test** : en hover (mode angle), on agite roll/pitch. En croisière tout est proche de zéro et illisible. On trace le set point (rouge), le gyro (blanc), et les termes par-dessus.

### Les quatre termes

`P = (set point − gyro) × gain_P` — recalculé toutes les **125 µs** (en 8K)

- **P (proportionnel)** — proportionnel à l'*erreur* (différence gyro/set point), pas au set point. Monter le gain pousse plus fort *et* plus vite dans le mouvement.
- **D (dérivée)** — pente (rate of change) du **gyro seul** dans Betaflight (pas de l'erreur). Signe inversé pour pousser à l'opposé. Plat = D≈0, pente raide = D max. Agit comme un **frein**. Bruité car dériver amplifie les écarts entre échantillons (le bruit *est* la raideur d'un échantillon au suivant).
- **I (intégrale)** — somme cumulée de l'erreur (aire sous la courbe). Inverse de la dérivée. Lent à monter *et* à redescendre → windup.
- **F (feed forward)** — pente du **set point seul** (les sticks). Ignore le gyro → **hors boucle PID**, ne connaît rien de l'erreur. Va directement au PID sum pour pousser le copter. Composante d'accélération (2ᵉ dérivée, « boost ») qui amplifie le jitter d'un signal radio bruité (ELRS 500 Hz en particulier).

> **Concept central.** Un contrôleur PID *utilise l'erreur pour annuler l'erreur* (analogie du casque à réduction de bruit : on rejoue le bruit inversé). Mais un drone a de l'inertie et du délai → il reste toujours un peu de P-error. L'**overshoot vient de l'inertie** (le drone tourne trop vite et se dépasse), pas du P en soi. L'overshoot survient *partout où le gyro dépasse le set point*, pas seulement au sommet d'un flip.

### Le PID sum

PID sum = P + I + D additionnés. Tuner = « sculpter » ce PID sum pour que gyro et set point restent parallèles sans overshoot. Le D **tempère** le push du P (recul un peu plus tôt). Trop de I allonge le push → fait continuer le gyro trop longtemps → overshoot dans la step response (souvent attribué à tort au D ; monter le master corrige).

> **Perception du pilote.** Aux vitesses élevées (400–800 °/s en plein flip), ce qui se passe « au milieu » du mouvement est largement hors de votre perception (le monde est flou). L'expérience réelle se joue à l'**entrée** et à la **sortie** du mouvement. Ne pas s'obséder sur les petites différences au centre.

---

## 2. Sliders et ordre des opérations
*Source : Q&A #22 — PID Fundamentals 2*

> **Règle d'or.** Toujours raisonner sur les **nombres** P/I/D, pas sur la position des sliders — plusieurs combinaisons donnent les mêmes valeurs. Le rôle des sliders est de *maintenir des relations constantes* (ex. le ratio P/D optimal) pendant qu'on manipule autre chose.

| Slider Betaflight | Ce qu'il change réellement |
|---|---|
| Damping | Gain du D (règle le P/D balance) |
| Tracking | P et I ensemble |
| Stick response | Feed forward |
| Dynamic damping | Dmax |
| Pitch (×2) | D et tracking pitch séparés (inertie rotationnelle plus grande en pitch sur frames freestyle) |
| Master multiplier | Scale tout en conservant les proportions |

### L'ordre de tuning de Brian

1. **Damping** → trouver le P/D balance optimal. Démarrer *sous-amorti* pour voir l'overshoot. Gros 7-8" : 1.0 → 1.6 ; petits 3-5" : démarrer bas (~0.6).
2. **Sliders pitch** → balance roll/pitch via la latence du premier wobble test.
3. **Master multiplier** → monter jusqu'à ne plus voir de baisse de latence (ou apparition d'oscillations / trilling).
4. **I term + feed forward** → selon la taille du drone (gros = moins de I ; ≤5" = remettre I à 1.0).

> **Relation P/I & windup.** Monter P (ou le master) **collapse l'erreur**, ce qui réduit le besoin en I et *autorise donc plus de I*. D'où l'ordre crucial : **master AVANT I**. Sinon : wobbles / overshoot prématurés, et on finit avec trop peu de I. Le I est très non-linéaire (« un P lent », montée *et* descente lentes), dur à lire dans les logs ; il donne un feeling « locked in » (surtout en virage / axe yaw).

### Dynamic damping

Deux D : un bas en croisière, un Dmax momentané sur mouvements brusques (déclenché par le gain factor). Utile surtout pour garder un D global bas tout en ayant du stopping power ponctuel → pertinent essentiellement sur **rigs lourds**. La composante gyro « ne marche pas vraiment » (n'aide ni le propwash ni la fluidité en forward flight). Réglages par défaut très doux (mode debug pour voir le D réel). À éviter sur très gros rigs (« asking for problems »).

> **Gros rigs / lifters.** D parfois >100, voire 2.0–2.5 en damping. Le danger vient des **arrêts/départs brusques** (saturation moteur, clipping, risque desync/stall), pas de la vitesse de rotation elle-même. Attention aux rates trop agressifs et au relâchement brutal du stick ; les courbes d'expo aggravent (« tout va bien jusqu'à ce que ça n'aille plus »).

---

## 3. Lire la step response
*Source : Q&A #23 — PID Fundamentals 3 + QuickTips « clean step response »*

La step response est le paramètre **le plus important** de PID Toolbox et le plus intuitif. Elle rend le tuning objectif. Une fenêtre glisse le long du log, calcule une step response sur chaque portion, puis **moyenne** toutes les courbes extraites.

### Préparer un test fiable

- **Neutraliser le feed forward** : stick response gain au minimum + feed forward transition = 1. Sinon le FF fausse l'algorithme (offset, overshoot initial exagéré, invisibles dans les traces brutes) et on ne sait pas si un overshoot vient du P ou du FF.
- **Couper le D min** : sinon le D varie selon l'amplitude des moves → courbes non reproductibles d'un test à l'autre.
- **Bons inputs** : bouger roll *et* pitch ensemble, mouvements amples et réguliers. Un faible signal/bruit (petits moves ou gyro bruité) = courbes bruitées → revoir d'abord le filtrage.

> **Astuce de rates (basement tuning).** Profil line-of-sight avec **max rate très bas (~250–300 °/s) mais center stick sensitivity forte** (ex. actual rates, sensitivity 200, max 250, voire linéaire). Force de gros mouvements sans que le copter parte hors contrôle. L'erreur classique : ~1000 °/s, où l'on pousse trop loin et le drone devient incontrôlable.

### Linéarité du système

En segmentant un log par amplitude (<50 °/s, ~50–100 °/s, ~150 °/s), les courbes sont **globalement identiques** ; seule la **latence** change légèrement sur les moves plus durs. Le système est donc *relativement linéaire* — c'est la condition qui rend la méthode valide (sinon chaque amplitude donnerait une courbe différente).

### Master multiplier & latence

L'élément à surveiller n'est pas le profil de courbe mais le **changement de latence**. La v0.8 ajoute des **barres d'erreur** (1 écart-type) = indice de fiabilité ; plus elles sont petites, mieux c'est. Une baisse de **2–3 ms est significative**. Faire des wobble tests longs et tester plusieurs valeurs (1.0, 1.2, 1.4, 1.6, 1.8) pour dégager une tendance.

### Interpréter les signatures

- **Bruit basse fréquence / broadband** : identifier d'abord sa fréquence (bouton « period » : distance entre deux pics, ex. ~30 Hz = 33 ms). Sous 100 Hz = broadband, difficile à éliminer ; c'est du bruit *gyro*, pas une oscillation.
- **Trilling / flutter moteur** : master trop haut. Compromis intéressant — supprime le propwash et reste invisible en vidéo, mais risque de chauffe si trop fort. Tester en vol (ex. master ~1.3 pitch, 1.1–1.2 roll) tant que les moteurs restent froids.

> **Terminologie.** Toute oscillation vient du **gyro** (ou set point + gyro), présente dans P *comme* dans D. Dire « P-term oscillation » ou « D-term oscillation » présuppose à tort un coupable unique. Brian parle de **« PID oscillation » / « P feedback »**. La source (bruit moteur, résonance, broadband) s'identifie au spectrogramme.

> **Feed forward = piège.** Le FF **exagère massivement l'overshoot** affiché, non représentatif de la réalité. Pire avec les RC rates élevés (ELRS 500 Hz) ; fiable à 50–100 Hz (Crossfire). En mode angle, le FF réglé dans l'onglet PID n'a aucun impact. → Toujours le neutraliser pour analyser.

---

## 4. Filtrage pour la performance
*Source : Q&A #16 — Filter Tuning for Performance*

> **Objectif.** Le but n'est **pas** le log le plus propre possible, mais de **réduire le filter delay** pour la performance de vol (propwash).

### Juger la propreté

Indicateurs : traces moteur fines et non dentelées, peu d'écart gyro/set point. Les **motor signals** (sorties en %) sont le signal envoyé aux **ESC** — pas les moteurs physiques — une transformation directe du PID sum :

`gyro + set point → PID → PID sum → motor outputs (1·2·3·4)`

Le **PID sum ≈ D term** : le D est le principal contributeur de bruit vers les moteurs. Métrique unique à surveiller : garder le **D term sous −10 dB** (idéal sous −20).

### Définir le bruit AVANT de filtrer

- Pics = harmoniques moteur (`RPM ÷ 60 = Hz`) + harmoniques 2 et 3. Avec 4 moteurs, les harmoniques élevées paraissent s'élargir.
- Outils : *RPM notches* (superpose le vrai RPM), *cumulative distribution* (plage de RPM au hover), *frequency-by-time / gray image* (spectrogramme).
- 2ᵉ harmonique faible sur tri-blade → un pic non-moteur = **résonance** (frame) à traiter au **dynamic notch**, pas au RPM filter.
- **Broadband / white noise** (plancher étalé sur toutes les fréquences) = problème **électrique ou gyro** → low-pass (jamais notch). Sous −30 dB, quasi invisible.

### Leviers de réduction du delay

| Action | Effet |
|---|---|
| Couper gyro low-pass 1 | Redondant avec le RPM filtering. Garder le low-pass 2 (anti-aliasing). |
| Monter le Q des RPM filters (500 → 1000) | Notches plus étroites = **moitié** du filter delay. Énorme sur X8 (72 notches actifs). |
| Baisser le weight de la 2ᵉ harmonique | La 1ʳᵉ coûte le plus, la 2ᵉ moins, la 3ᵉ quasi rien (garder la 3ᵉ). |

Résultat mesuré sur l'exemple : gyro delay **2.63 → 1.18 ms** (« la différence entre un quad correct et un très bon »). Cible 5" : **≤ 1.5 ms** de gyro delay.

> **Pourquoi 1.5–2 ms ? (mécanisme).** Le delay ne concerne pas la réaction du pilote ni la vidéo, mais le **déphasage de P et D** à la fréquence de propwash (~30 Hz). Les filtres unidirectionnels temps-réel introduisent forcément du delay. Si P est retardé jusqu'à ~180°, il pousse *en phase* avec l'oscillation → **feedback positif** (trilling / flyaway), à condition d'avoir assez de gain. Repère : à 50 Hz, `1 ms ≈ 18°` (2 ms = 36°, 4 ms = 72°).

Tolérance croissante avec la taille : un 10" lifter encaisse 4–5 ms (latence de réponse propre déjà lente). Le **feed forward** ne réduit que la latence d'entrée (sticks→gyro) et **n'affecte ni le propwash ni le PID controller**.

> **Sécurité (Q élevé / props cassées).** Pas de preuve solide qu'un Q de 1000 augmente le risque moteur. Un **prop strike** fait grimper l'ampérage et peut griller un moteur indépendamment des filtres → en cas de jello massif : désarmer et ramasser le quad (sauf en race). Plus sûr d'ajouter des **dynamic notches** que de s'inquiéter du Q des RPM filters.

---

## 5. Récapitulatif & pièges récurrents

Fil conducteur de la série : **comprendre comment chaque terme se calcule** (#21) permet de **savoir quel slider manipuler et dans quel ordre** (#22), ce qui permet enfin de **lire correctement la step response** (#23 + QuickTips) ; le filtrage (#16) conditionne la performance en jouant sur le filter delay.

> **Les 3 pièges qui reviennent à chaque épisode**
> - **Sous-tuner** et sous-estimer le master multiplier (wobbles à bas throttle parce que le gain est trop bas → pousser le master).
> - **Tuner le I avant le master** → overshoot prématuré, on finit avec trop peu de I.
> - **Laisser le feed forward fausser** la lecture de la step response.

> **Les deux paramètres primordiaux**
> Le **P/D balance** et le **P/D gain (master)**. I et FF ne servent qu'à « nettoyer » et affiner ensuite. Côté filtres, une seule métrique à surveiller en priorité : le **D term sous −10 dB**, et un seul objectif : **minimiser le filter delay**.

---

*Synthèse personnelle compilée à partir des transcriptions auto-générées des sessions PID Toolbox (Brian) — Q&A #16, #21, #22, #23 et QuickTips « step response ». Document de référence interne FPVLogForge.*
