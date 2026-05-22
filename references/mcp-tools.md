# MCP Tools — Betaflight FC en direct

Le serveur MCP Betaflight (`betaflight-mcp`) expose les tools MSP via FastMCP. Quand il est disponible, **toujours préférer la lecture live à demander à l'utilisateur de coller un diff** — c'est plus fiable et plus rapide.

## Détecter si le MCP est disponible

Tenter un appel à `list_serial_ports` : s'il répond, le serveur est actif. S'il échoue, basculer en mode offline (diff CLI) sans bloquer.

## Catalogue des tools

### Connexion (toujours en premier)

| Tool | Quand l'utiliser |
|------|-----------------|
| `list_serial_ports` | Déterminer le port avant de connecter |
| `connect(port, baudrate)` | Ouvrir la session MSP — une seule fois par conversation |
| `disconnect` | Fin de session ou avant de passer à un autre FC |

Proposer le port détecté à l'utilisateur, ne pas connecter sans confirmation.

### Lecture de l'état courant

Appeler ces tools **avant** de proposer tout changement — ne jamais travailler à l'aveugle :

| Tool | Données retournées | Cas d'usage |
|------|-------------------|-------------|
| `get_board_info` | Firmware, variante, MCU, version API | Identifier le FC, vérifier la version BF |
| `get_fc_status` | Arming flags, cycle time, charge CPU | Diagnostiquer un problème d'armement |
| `get_pid_values` | P/I/D par axe (roll, pitch, yaw, level) | Avant tout ajustement PID |
| `get_rates` | rc_rate, expo, superrate, throttle | Avant tout ajustement rates |
| `get_filter_config` | Gyro lowpass/notch, Dterm, RPM filter | Diagnostiquer oscillations / bruit |
| `get_pid_advanced` | Feedforward, anti-gravity, TPA, iterm relax, D-Max | Tuning avancé |
| `get_advanced_config` | Protocole ESC (DSHOT), PID dénominateurs, PWM rate | Vérifier DSHOT, looptime |
| `get_feature_config` | Features actives (AIRMODE, GPS, LED…) | Vérifier la config features |
| `get_modes` | AUX switches et plages µs | Diagnostiquer modes RC |
| `get_sensor_config` | Accéléro, baro, magnéto | Problèmes de capteurs |

### Télémétrie temps réel

À utiliser pour le diagnostic live, pas pour la configuration :

| Tool | Données retournées | Cas d'usage |
|------|-------------------|-------------|
| `get_imu_data` | Accéléro (g), gyro (°/s), magnéto | Vérifier vibrations au sol |
| `get_attitude` | Roulis, tangage, cap (°) | Vérifier l'horizon artificiel |
| `get_battery` | Tension (V), courant (A), mAh, RSSI | Diagnostic batterie |
| `get_battery_state` | Cellules, capacité, état (OK/WARNING/CRITICAL) | Alerte batterie faible |
| `get_rc` | Canaux RC (µs) | Vérifier la réception radio |
| `snapshot_rc_delta(baseline, threshold)` | Canaux qui ont bougé au-delà du seuil | Identifier quel switch/stick est actif |
| `measure_rc_noise(duration_s, channels)` | Bruit 95e percentile + deadband suggéré | Diagnostiquer bruit RC sticks au repos |
| `get_motors` | Sorties moteurs (µs) | Vérifier les moteurs au sol (props-off) |

### Écriture — pattern obligatoire

**Toujours suivre cet ordre, sans exception :**

```
1. get_pid_values / get_rates / get_filter_config   ← lire l'état actuel
2. Calculer les nouvelles valeurs
3. Présenter le résumé à l'utilisateur et demander confirmation explicite
4. set_pid_values / set_rates                        ← appliquer après confirmation
5. save_config                                       ← sauvegarder en EEPROM
```

Ne jamais enchaîner `set_*` + `save_config` sans confirmation intermédiaire. `save_config` redémarre le FC.

| Tool | Paramètres | Contraintes |
|------|-----------|-------------|
| `set_pid_values(axis, p, i, d)` | `axis` : roll/pitch/yaw/level — `p`,`i`,`d` : 0–255 | Vérifier les plages dans `references/parameters.md` |
| `set_rates(rc_rate, rc_expo, roll_rate, …)` | Tous optionnels — seuls les fournis sont mis à jour | Idem |
| `save_config` | Aucun | Provoque un reboot FC — prévenir l'utilisateur |
| `reboot_fc` | Aucun | Utiliser seulement si explicitement demandé |

## Gestion des erreurs

- Tool retourne `{"error": "..."}` → signaler à l'utilisateur, ne pas continuer l'écriture.
- `connect` échoue → proposer un autre port de `list_serial_ports`, ou basculer offline.
- `set_pid_values` retourne `{"errors": [...]}` → afficher les erreurs, ne pas appeler `save_config`.
- Serveur MCP indisponible → continuer en mode offline (diff CLI) sans bloquer.
