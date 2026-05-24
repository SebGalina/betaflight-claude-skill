# MCU & Drivers USB — Betaflight Flight Controllers

Ce guide recense les MCU courants sur les FCs Betaflight, les drivers USB nécessaires par OS, et les procédures pour ne plus galérer à trouver le bon driver.

## Identifier le MCU de son FC

### Via Betaflight Configurator
Onglet **Setup** → ligne `MCU` (affiché après connexion).

### Via CLI
```
version
```
Exemple de sortie : `# Betaflight / STM32F405 (S405) 4.5.1`  
Le code entre parenthèses (`S405`, `S7X2`, `H743`…) identifie le MCU.

### Sans connexion (FC inconnu)
- Chercher le nom du FC sur [betaflight.com/docs/wiki/boards](https://betaflight.com/docs/wiki/boards)
- Ou chercher `[nom du FC] betaflight target` sur Google

---

## Tableau des MCU courants

| MCU | Fabricant | Fréquence | Présence | Notes |
|-----|-----------|-----------|----------|-------|
| STM32F405 | ST Microelectronics | 168 MHz | Très répandu (FCs milieu de gamme) | Référence depuis BF 3.x |
| STM32F411 | ST Microelectronics | 100 MHz | Répandu (FCs budget STM) | Moins puissant que F405 |
| STM32F722 | ST Microelectronics | 216 MHz | Répandu (FCs milieu/haut) | F7, meilleure perf filtres |
| STM32F745 | ST Microelectronics | 216 MHz | Moins courant | F7 avec plus de RAM |
| STM32H743 | ST Microelectronics | 480 MHz | Haut de gamme | H7, supporte 8kHz + RPM |
| STM32H750 | ST Microelectronics | 480 MHz | Haut de gamme | H7, flash externe |
| STM32G473 | ST Microelectronics | 170 MHz | Émergent | G4, bon rapport perf/prix |
| **AT32F435** | Artery Technology | 288 MHz | **Très répandu (FCs budget chinois)** | Clone STM32 — **driver différent** |
| AT32F437 | Artery Technology | 288 MHz | Répandu | Variante AT32 avec plus de RAM |
| APM32F405 | Geehy Semiconductor | 168 MHz | Présent | Compatible STM32F4 |
| GD32F405 | GigaDevice | 168 MHz | Présent | Compatible STM32F4 |

---

## Drivers par MCU et par OS

### STM32 (F4, F7, G4, H7) — Le cas standard

#### Windows
Deux drivers distincts selon le mode du FC :

**Mode normal (VCP — port COM virtuel) :**
- Windows 10/11 : auto-détecté dans la majorité des cas (driver CDC ACM intégré)
- Si absent : installer **STM32 Virtual COM Port Driver** depuis [st.com](https://www.st.com/en/development-tools/stsw-stm32102.html)
- Ou utiliser **ImpulseRC Driver Fixer** (voir section Outils) — résout automatiquement VCP + DFU

**Mode DFU (bootloader — pour flasher) :**
- Le FC apparaît comme `STM32 BOOTLOADER` dans le Gestionnaire de périphériques
- USB VID/PID : `0x0483` / `0xDF11`
- Driver requis : **WinUSB** (via Zadig) ou **STM32 DFU** (via ImpulseRC Driver Fixer)
- Sans ce driver, Betaflight Configurator ne détecte pas le FC en DFU et refuse de flasher

Procédure Zadig pour DFU :
1. Mettre le FC en mode DFU (bouton BOOT + reset, ou via CLI `bl`)
2. Ouvrir Zadig → Options → List All Devices
3. Sélectionner `STM32 BOOTLOADER`
4. Choisir driver `WinUSB` → Install Driver

#### macOS
- Mode VCP : aucun driver requis, port `/dev/tty.usbmodem*` apparaît automatiquement
- Mode DFU : aucun driver requis, Betaflight Configurator détecte directement

#### Linux
- Mode VCP : module `cdc_acm` intégré au noyau, port `/dev/ttyACM0` (ou `ttyACM1`…)
- Accès sans `sudo` : ajouter l'utilisateur au groupe `dialout`
  ```bash
  sudo usermod -a -G dialout $USER
  # puis se déconnecter/reconnecter
  ```
- Mode DFU : `dfu-util` + règles udev
  ```bash
  sudo apt install dfu-util
  # Règle udev pour STM32 DFU :
  echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0483", ATTR{idProduct}=="df11", MODE="0664", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/49-stm32-dfu.rules
  sudo udevadm control --reload-rules
  ```

---

### AT32F435 / AT32F437 — Le cas qui fait galérer

L'AT32 est fabriqué par **Artery Technology** (华芯微特), une société chinoise. Il est de plus en plus utilisé sur les FCs budget (KayouMini, etc.) car moins cher que STM32 à performances équivalentes. **Son stack USB est différent de STM32** — les drivers STM32 ne fonctionnent pas.

#### USB IDs
| Mode | VID | PID |
|------|-----|-----|
| VCP (mode normal) | `0x2E3C` | `0x5740` (typique) |
| DFU (bootloader) | `0x2E3C` | `0x4004` (typique) |

> ⚠️ Ces IDs peuvent varier selon la version du firmware AT32. Vérifier dans le Gestionnaire de périphériques Windows ou avec `lsusb` sous Linux.

#### Windows

**Option A — Driver Artery officiel (recommandé) :**
1. Aller sur [arterychip.com](https://www.arterychip.com/en/support/tools.jsp)
2. Chercher "USB VCP Driver" dans la section Tools & Drivers
3. Installer le package AT32 VCP Driver
4. Le FC doit apparaître comme port COM dans le Gestionnaire de périphériques

**Option B — Zadig (si Option A échoue ou indisponible) :**
1. Télécharger [Zadig 2.9](https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe)
2. FC branché en mode normal → Options → List All Devices
3. Repérer le device AT32 (VID `2E3C`)
4. Assigner driver `WinUSB`
5. Répéter pour le mode DFU si nécessaire

> ⚠️ **ImpulseRC Driver Fixer ne gère pas AT32** — il est conçu pour STM32 uniquement. Ne pas l'utiliser pour les FCs AT32.

#### macOS
- Généralement reconnu automatiquement comme port série USB CDC
- Si absent : vérifier `ls /dev/cu.usbmodem*` après branchement
- Aucun driver tiers normalement nécessaire

#### Linux
- Module `cdc_acm` fonctionne
- Règle udev spécifique AT32 :
  ```bash
  echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2e3c", MODE="0664", GROUP="plugdev"' | sudo tee /etc/udev/rules.d/49-at32-usb.rules
  sudo udevadm control --reload-rules
  ```

---

### APM32F405 / GD32F405 — Clones STM32F4

Ces MCU sont des clones compatibles STM32F4. **Les drivers STM32 fonctionnent** (même VID/PID DFU `0x0483/0xDF11` dans certains cas, ou similaire).
- Traiter comme un STM32F405 pour les drivers
- Si ça ne fonctionne pas avec ImpulseRC Driver Fixer, utiliser Zadig + WinUSB

---

## Outils indispensables

### ImpulseRC Driver Fixer (Windows)
Le moyen le plus simple de corriger les drivers STM32 sur Windows. Résout en un clic :
- STM32 VCP (port COM)
- STM32 DFU (mode bootloader)

Téléchargement : [impulserc.com/pages/downloads](https://impulserc.com/pages/downloads)

> ⚠️ Ne pas utiliser pour AT32 — conçu pour STM32 uniquement.

### Zadig (Windows)
Outil universel pour assigner manuellement un driver USB (WinUSB, libusb) à n'importe quel périphérique. Indispensable pour AT32 et pour les cas où Driver Fixer échoue.

Téléchargement : [zadig.akeo.ie](https://zadig.akeo.ie) — [zadig-2.9.exe direct](https://github.com/pbatard/libwdi/releases/download/v1.5.1/zadig-2.9.exe)

Procédure générale :
1. Brancher le FC (mode normal ou DFU selon le cas)
2. Ouvrir Zadig → Options → **List All Devices**
3. Sélectionner le bon device dans la liste
4. Choisir `WinUSB` comme driver cible
5. Cliquer **Install Driver** ou **Replace Driver**

### dfu-util (Linux / macOS)
Outil CLI pour flasher via DFU sans passer par le Configurator.
```bash
# Linux
sudo apt install dfu-util

# macOS
brew install dfu-util
```

---

## Diagnostic rapide

**Le FC ne s'affiche pas du tout (aucun port COM, aucun device USB) :**
- Câble USB data (pas charge-only) ?
- Essayer un autre port USB
- Vérifier l'alimentation du FC (certains FC nécessitent une batterie pour USB)
- Windows : ouvrir le Gestionnaire de périphériques → "Autres périphériques" → chercher un device inconnu

**Le FC s'affiche mais Betaflight Configurator ne le voit pas :**
- Driver VCP manquant → ImpulseRC Driver Fixer (STM32) ou Zadig (AT32)
- Linux : permission refusée → `sudo usermod -a -G dialout $USER`

**Le FC est détecté mais impossible de flasher (DFU échoue) :**
- Pas en mode DFU → maintenir BOOT pendant le reset ou utiliser `bl` dans le CLI
- Driver DFU manquant → Zadig → WinUSB sur `STM32 BOOTLOADER` ou device AT32 DFU
- AT32 + Windows : s'assurer que Zadig a bien remplacé le driver sur le device DFU (VID `2E3C`)

**"No DFU device found" dans le Configurator :**
- STM32 : driver WinUSB non installé pour le mode DFU → Zadig
- AT32 : idem, mais avec VID Artery

---

## FCs connus et leur MCU

| FC | MCU | Notes driver |
|----|-----|-------------|
| KayouMini | AT32F435G | Driver Artery requis sur Windows |
| SpeedyBee F405 V3/V4 | STM32F405 | STM32 standard |
| SpeedyBee F7 | STM32F722 | STM32 standard |
| Mateksys F405 | STM32F405 | STM32 standard |
| Mateksys H743 | STM32H743 | STM32 standard |
| BetaFPV F4 | STM32F411 | STM32 standard |
| Foxeer F745 | STM32F745 | STM32 standard |
| Holybro Kakute H7 | STM32H743 | STM32 standard |

> Cette liste est non exhaustive. Toujours vérifier via `version` dans le CLI ou la doc du fabricant.
