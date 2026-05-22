# Setup Wizard — Configuration initiale d'un drone

Ce wizard est déclenché **uniquement** sur demande explicite (voir triggers dans SKILL.md). Il guide l'utilisateur de zéro jusqu'à une configuration de départ appliquée ou exportée.

## Étape 1 — Collecter les infos build

Poser ces questions en **une seule fois**, regroupées :

| Info | Exemples |
|------|----------|
| Type et taille de frame | 3" cinewhoop, 5" freestyle, 7" longrange, tinywhoop… |
| Moteurs (KV) | 2306 2450KV, 1404 3800KV… |
| Taille d'hélices | 5×4.3×3, 3×2×3… |
| Batterie (S) | 3S, 4S, 6S |
| Protocole ESC | DSHOT300, DSHOT600 — si inconnu, rester sur DSHOT300 |
| Protocole récepteur | ELRS, CRSF, SBUS, FPort, iBUS… |
| Style de vol | Freestyle, racing, cinématique, longrange |
| Niveau | Débutant, intermédiaire, confirmé |

Ne pas redemander ce que l'utilisateur a déjà fourni dans son message initial.

## Étape 2 — Connexion au FC (optionnel)

Demander si le FC est branché en USB :

- **Oui** → `list_serial_ports` → proposer le port détecté → `connect` → `get_board_info` pour confirmer la version firmware.
- **Non / MCP indisponible** → mode offline : générer un diff CLI à coller dans Betaflight Configurator.

Si `connect` échoue : basculer automatiquement en mode offline sans bloquer.

## Étape 3 — Charger le preset

Sélectionner dans `assets/presets/` :

| Build | Preset |
|-------|--------|
| 3" cinewhoop / nano | `cinewhoop-3inch.txt` |
| 5" freestyle / racing | `5inch-freestyle.txt` |
| 7" / longrange | `longrange-7inch.txt` |

Si aucun preset ne correspond exactement, prendre le plus proche et le signaler.

## Étape 4 — Appliquer ou exporter

**Mode connecté (MCP disponible) :**

1. Lire l'état actuel : `get_pid_values`, `get_rates`, `get_filter_config`
2. Calculer les valeurs cibles depuis le preset + infos build
3. Présenter le résumé des changements et demander confirmation explicite
4. Appliquer : `set_pid_values` (roll, pitch, yaw) + `set_rates`
5. `save_config` → prévenir l'utilisateur que le FC va redémarrer

**Mode offline :**

```
# Tune de base — [type de build] — généré par Betaflight Assistant
# Betaflight [version] · [date]
set pid_roll_p = ...
set pid_roll_i = ...
...
save
```

## Règles

- Ne jamais appeler `save_config` sans confirmation explicite de l'utilisateur.
- Rappeler de retirer les hélices avant tout test moteur.
- Si une valeur sort des plages safe de `references/parameters.md`, le signaler et demander confirmation.
- Le wizard produit une **configuration de départ**, pas un tune final. Toujours recommander un vol de test et un ajustement PID progressif.
