# betaflight-skill

> **Claude skill for Betaflight: FPV drone configuration, PID tuning, log analysis, and troubleshooting.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Betaflight](https://img.shields.io/badge/Betaflight-4.5.x-orange.svg)](https://betaflight.com/)
[![Claude Skill](https://img.shields.io/badge/Claude-Agent_Skill-purple.svg)](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)

Un skill [Claude](https://claude.ai) qui aide à configurer, tuner, analyser et dépanner les drones FPV sous firmware Betaflight.

## ✨ Ce que fait le skill

Une fois chargé, ce skill permet à Claude de :

- 🔧 **Diagnostiquer les problèmes de vol** à partir de descriptions en langage naturel (wobbles, moteurs chauds, oscillations, drift, propwash)
- 📄 **Parser et analyser les fichiers CLI diff/dump** partagés par l'utilisateur
- ⚡ **Générer des configurations CLI prêtes à coller** pour les classes de builds courantes (5" freestyle, 3" cinewhoop, 7" longrange)
- 🔄 **Migrer les configurations** entre versions majeures de Betaflight (4.4 → 4.5 → 4.6)
- ⚠️ **Repérer les paramètres dépréciés** qui causeraient des erreurs à l'import sur un firmware plus récent
- 📏 **Recommander des plages de valeurs sûres** pour PIDs, filtres, rates et paramètres ESC

**Firmware cible** : Betaflight 4.5.x (différences pour 4.4 et 4.6 documentées dans les références).

## 🚀 Installation

### claude.ai (web / mobile / desktop)

1. Télécharger l'archive `.skill` depuis la dernière [release](../../releases) (ou zipper le dossier `betaflight/`)
2. Dans Claude : **Settings → Capabilities → Skills → "+ Create skill"**
3. Uploader le fichier
4. Le skill se déclenche automatiquement quand il est pertinent

### Claude Code

```bash
# Installation personnelle
cp -r betaflight ~/.claude/skills/

# Ou par projet
cp -r betaflight .claude/skills/
```

### Claude API

Upload via la Skills API — voir la [documentation officielle](https://platform.claude.com/docs/en/build-with-claude/skills-guide).

## 📦 Structure

```
betaflight/
├── SKILL.md                  Définition principale + description de déclenchement
├── references/               Docs chargées à la demande
│   ├── cli-commands.md       Syntaxe CLI Betaflight
│   ├── parameters.md         Paramètres `set` avec plages sûres
│   ├── pid-tuning.md         Guide PID, filtres et rates
│   ├── troubleshooting.md    Diagnostic par symptôme
│   └── version-changes.md    Notes de migration entre versions
├── scripts/                  Outils Python
│   ├── parse_diff.py         Parser pour CLI diff/dump
│   ├── validate_config.py    Validation de cohérence
│   └── analyze_blackbox.py   Analyse header-level de logs blackbox
├── assets/
│   └── presets/              Configs CLI de départ
│       ├── 5inch-freestyle.txt
│       ├── cinewhoop-3inch.txt
│       └── longrange-7inch.txt
└── evals/                    Cas de test
    ├── evals.json
    └── sample_diff.txt
```

## 💬 Exemples d'utilisation

Une fois le skill installé, vous pouvez simplement écrire à Claude :

> « Mon drone 5 pouces wobble en yaw depuis que j'ai changé d'hélices, que faire ? »

> « Génère-moi une config CLI de base pour un 5\" freestyle avec moteurs 2207 1750KV en 6S, FC F7, ELRS sur UART2 »

> « Voici mon diff Betaflight, regarde si tout est cohérent » *(en attachant le fichier)*

> « Je passe de Betaflight 4.4 à 4.5, quels paramètres je dois revoir ? »

Le skill se déclenche automatiquement — pas besoin de l'invoquer explicitement.

## 🧪 Tests

Pour tester les scripts :

```bash
python scripts/parse_diff.py evals/sample_diff.txt
python scripts/validate_config.py evals/sample_diff.txt
```

Les cas de test du skill sont dans `evals/evals.json`.

## ⚠️ Limites

- **L'analyse blackbox est superficielle.** Pour une vraie analyse FFT/PID, utilisez [blackbox.betaflight.com](https://blackbox.betaflight.com) ou PIDtoolbox.
- **Cible Betaflight 4.5.x par défaut.** Les configurations 4.4 peuvent contenir des paramètres dépréciés ; le skill les signale mais ne migre pas automatiquement.
- **Pas de communication temps réel avec le FC.** Ce skill travaille sur des fichiers et des descriptions — il ne dialogue pas avec un FC connecté en USB.

## 🛡️ Sécurité

Ce skill suit des règles strictes :

- Ne recommande **jamais** de désactiver les failsafes ou les contrôles d'armement
- **Avertit toujours** avant des changements de direction / mapping moteur (test props-off)
- **Signale** les valeurs suspectes plutôt que de les appliquer silencieusement
- **Rappelle** que les nouveaux tunes doivent être testés dans une zone sûre

## 🤝 Contribuer

Voir [CONTRIBUTING.md](CONTRIBUTING.md). Issues et PRs bienvenues, en français comme en anglais.

## 📜 Licence

Apache 2.0 — voir [LICENSE.txt](LICENSE.txt).

## 🔗 Liens utiles

- [Betaflight](https://betaflight.com/) — projet officiel
- [Documentation Betaflight](https://betaflight.com/docs)
- [Claude Agent Skills (spec)](https://agentskills.io/)
- [Documentation Claude Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)

## ⚖️ Avertissement

Projet communautaire non affilié au projet Betaflight ni à aucun fabricant de FC. Betaflight est une marque de ses détenteurs respectifs. Ce skill s'appuie sur des conventions Betaflight publiquement documentées.
