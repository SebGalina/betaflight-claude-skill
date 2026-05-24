# Arming Prevention Flags — Betaflight

Source: https://betaflight.com/docs/wiki/guides/current/Arming-Sequence-And-Safety

Depuis Betaflight 3.2, le système de prévention d'armement expose des flags précis qui indiquent pourquoi le FC refuse de s'armer. Ils sont visibles dans :
- le CLI (`status`)
- l'OSD (affiché en vol si armement échoue)
- les bips buzzer (voir ci-dessous)
- Betaflight Configurator (onglet Status)

## Lecture via CLI

```
status
```

La ligne `Arming disable flags:` liste tous les flags actifs. Si elle est absente ou vide, le FC est armable.

## Décodage par buzzer

Signal : **5 bips courts** d'attention, puis bips longs + bips courts espacés.

Formule : `code = (nb_longs × 5) + nb_courts`

Exemple : 1 long + 2 courts = code 7 (`CRASH`)

## Tableau des flags

| Flag | Code | Cause | Solution |
|------|------|-------|----------|
| `NOGYRO` | 1 | Gyroscope non détecté au démarrage | Problème matériel ou firmware — reflasher, vérifier soudures |
| `FAILSAFE` | 2 | Failsafe en cours d'exécution | Attendre fin du failsafe, vérifier signal RX |
| `RXLOSS` / `RX_FAILSAFE` | 3 | Signal récepteur absent ou invalide | Vérifier liaison radio, binding, UART RX |
| `BADRX` / `NOT_DISARMED` | 4 | RX en recovery ET switch arm déjà ON | Désactiver le switch d'armement avant mise sous tension |
| `BOXFAILSAFE` | 5 | Switch failsafe activé sur la télécommande | Désactiver le switch failsafe |
| `RUNAWAY` | 6 | Runaway Takeoff Prevention déclenché | Désarmer, vérifier PIDs et configuration moteurs |
| `CRASH` | 7 | Crash Recovery actif | Désarmer |
| `THROTTLE` | 8 | Throttle au-dessus de `min_check` à l'armement | Baisser le throttle en dessous de `min_check` (défaut ~1050 µs) |
| `ANGLE` | 9 | FC incliné au-delà de `small_angle` | Poser le drone à plat — défaut 25°, configurable via `set small_angle` |
| `BOOTGRACE` | 10 | Tentative d'armement trop rapide après mise sous tension | Attendre `pwr_on_arm_grace` secondes (défaut 5 s) |
| `NOPREARM` | 11 | Prearm switch configuré mais non activé | Activer le prearm switch en premier |
| `LOAD` | 12 | Charge CPU trop élevée | Désactiver des fonctionnalités, réduire la fréquence gyro/PID |
| `CALIB` | 13 | Calibration des capteurs en cours | Attendre la fin de la calibration |
| `CLI` | 14 | Session CLI ouverte | Taper `exit` dans le CLI |
| `CMS` | 15 | Menu de configuration OSD (CMS) ouvert | Quitter le menu CMS |
| `OSD` | 16 | Menu OSD actif | Quitter le menu OSD |
| `BST` | 16 | Télémétrie Black Sheep (BST) désarmée | Consulter la doc du matériel BST |
| `MSP` | 17 | Connexion MSP active (Betaflight Configurator ouvert) | Déconnecter le Configurator |
| `PARALYZE` | 18 | Mode Paralyze activé (désarmement permanent) | Redémarrer le FC |
| `GPS` | 19 | GPS Rescue configuré mais pas assez de satellites | Attendre le fix GPS (≥ satellites requis) ou désactiver GPS Rescue |
| `RESCUE_SW` | 20 | Switch GPS Rescue en position active avant armement | Désactiver le switch GPS Rescue |
| `RPMFILTER` / `DSHOT_TELEM` | 21 | RPM filter activé mais télémétrie DSHOT invalide | Vérifier `dshot_bidir = ON`, firmware BLHeli_32/AM32, câblage moteurs |
| `REBOOT_REQD` | 22 | Un changement de configuration nécessite un redémarrage | Redémarrer le FC (`reboot` dans le CLI ou débrancher) |
| `DSHOT_BBANG` | 23 | DSHOT Bitbang en échec | Conflit de timers — passer sur un protocole non-Bitbang ou vérifier la config `resource` |
| `NO_ACC_CAL` | 24 | Accéléromètre jamais calibré | Calibrer l'accéléromètre (onglet Setup du Configurator) ou désactiver les modes qui en dépendent |
| `MOTOR_PROTO` | 25 | Protocole moteur/ESC non sélectionné | Choisir un protocole (DSHOT300, DSHOT600…) dans l'onglet Configuration |
| `ARMSWITCH` | 26 | Switch d'armement en position armée au démarrage | Position neutre du switch arm avant mise sous tension |

## Flags les plus courants et leurs pièges

### `RXLOSS` (3)
Le plus fréquent. Causes typiques :
- Binding non effectué ou perdu
- Mauvaise configuration UART (mauvais numéro de port, baud rate)
- `serialrx_provider` ne correspond pas au protocole RX (ex : CRSF configuré mais récepteur SBUS)
- Portée dépassée en extérieur

### `MSP` (17)
Betaflight Configurator maintient une connexion MSP active tant qu'il est ouvert. **Déconnecter le Configurator** (bouton Disconnect) avant d'essayer d'armer. Le flag disparaît immédiatement.

### `RPMFILTER` (21)
Nécessite :
1. `set dshot_bidir = ON`
2. ESC avec firmware supportant la télémétrie bidirectionnelle (BLHeli_32 ≥ 32.7, AM32, Bluejay)
3. `motor_poles` correctement configuré (typiquement 14 pour un 2204–2307)

### `ANGLE` (9)
Par défaut le FC refuse de s'armer s'il est incliné de plus de 25°. Configurable :
```
set small_angle = 180   # désactive la contrainte d'angle (non recommandé)
set small_angle = 25    # valeur par défaut
```

### `ARMSWITCH` (26)
Sécurité critique : si le switch d'armement est déjà en position armée lors de la mise sous tension, le FC bloque l'armement. Toujours décoller avec le switch en position désarmée.

### `DSHOT_BBANG` (23)
Le mode Bitbang utilise les timers DMA directement. Conflits fréquents avec :
- LED strip sur certains FC
- Certaines configurations `resource` personnalisées
Solution : désactiver le LED strip ou passer sur DSHOT via hardware timer (si disponible sur le FC).
