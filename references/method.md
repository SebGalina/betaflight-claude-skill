Dans PIDToolBox, le Step Response (réponse indicielle) sert à visualiser comment un système réagit lorsqu’on applique une entrée brusque, typiquement un échelon de valeur 1.

1. Que représente la courbe ?
Abscisse (axe X)

Le temps :

t(secondes)
Ordonnée (axe Y)

La sortie du système :

y(t)

C’est la réponse du procédé après application de l’échelon.

Par exemple :

entrée : passe brutalement de 0 à 1
sortie : moteur, température, vitesse, tension, etc.

Le graphe montre :

rapidité
dépassement
oscillations
stabilité
2. Mathématiquement : qu’est-ce qu’un “step” ?

Un step est la fonction échelon unité :

u(t)=1pour t≥0

Le système reçoit une entrée constante instantanée.

3. Comment PIDToolBox calcule ça ?

Le logiciel part généralement de la fonction de transfert :

G(s)

et calcule :

Y(s)=G(s)×
s
1
	​


car la transformée de Laplace d’un échelon vaut :

L{u(t)}=
s
1
	​


Ensuite il fait la transformée inverse de Laplace pour obtenir :

y(t)
4. Exemple simple

Supposons :

G(s)=
τs+1
1
	​


(c’est un système du premier ordre)

La réponse indicielle devient :

y(t)=1−e
−t/τ

Voici la forme typique :

y(t)=1−e
−t/τ

au début : y(0)=0
puis la sortie monte progressivement
à long terme : y(t)→1
5. Comment recalculer ça sur ton PC ?
Méthode Python (la plus simple)

Avec control + matplotlib.

Installation :

pip install control matplotlib numpy

Puis :

import control as ctl
import matplotlib.pyplot as plt

# Fonction de transfert :
# G(s) = 1 / (s + 1)

num = [1]
den = [1, 1]

sys = ctl.TransferFunction(num, den)

# Réponse indicielle
t, y = ctl.step_response(sys)

# Affichage
plt.plot(t, y)
plt.xlabel("Temps (s)")
plt.ylabel("Sortie y(t)")
plt.title("Step Response")
plt.grid(True)
plt.show()
6. Si tu veux le recalcul “manuel”

Pour un système :

G(s)=
A(s)
B(s)
	​


tu fais :

Étape 1

Multiplier par l’échelon :

Y(s)=G(s)
s
1
	​

Étape 2

Décomposition en éléments simples

Étape 3

Transformée inverse de Laplace

Exemple :

s(s+1)
1
	​


devient :

s
1
	​

−
s+1
1
	​


donc :

y(t)=1−e
−t
7. Dans un PID : pourquoi c’est utile ?

La step response permet de voir :

si le PID est stable
s’il oscille
le temps de montée
le dépassement
l’erreur statique

Donc c’est LE graphe principal pour régler :

K
p
	​

K
i
	​

K
d
	​

8. Interprétation rapide
Bonne réponse
monte vite
peu d’oscillation
atteint la consigne
Mauvaise réponse
oscillations fortes
dépassement énorme
divergence
9. Cas général d’un PID

Le système fermé devient souvent :

T(s)=
1+C(s)G(s)
C(s)G(s)
	​


où :

G(s) = procédé
C(s) = PID

avec :

C(s)=K
p
	​

+
s
K
i
	​

	​

+K
d
	​

s

Puis la step response est :

Y(s)=T(s)
s
1
	​


et on calcule y(t).

Si tu veux, je peux aussi te montrer :

comment PIDToolBox calcule numériquement la courbe,
comment faire le calcul sans Laplace (Euler / Runge-Kutta),
comment retrouver les métriques (overshoot, settling time, rise time),
ou 
comment coder exactement un simulateur PID complet en Python/C++.

PIDToolBox ne “résout” généralement pas l’équation symboliquement à chaque fois.
Il fait une simulation numérique discrète dans le temps.

En pratique :

il découpe le temps en petits pas :
Δt
il calcule l’état du système à chaque instant :
t
0
	​

, t
1
	​

, t
2
	​

, ...
il applique le PID à chaque pas.

C’est exactement ce qu’on ferait dans un automate, microcontrôleur ou DSP réel.

1. Le principe général

Supposons un système :

x
˙
=Ax+Bu
y=Cx+Du

x
˙
=Ax+Bu

où :

x = état interne
u = commande
y = sortie
2. Le “step”

Pour une réponse indicielle :

r(t)=1

(consigne constante)

3. Boucle PID numérique

À chaque pas :

erreur
e(t)=r(t)−y(t)

e(t)=r(t)−y(t)

PID continu
u(t)=K
p
	​

e(t)+K
i
	​

∫e(t)dt+K
d
	​

dt
de
	​


u(t)=K
p
	​

e(t)+K
i
	​

∫e(t)dt+K
d
	​

dt
de
	​


Mais un PC ne calcule pas ça “continuement”.

Il discrétise.

4. Discrétisation numérique

À l’instant k :

t
k
	​

=kΔt
Intégrale

Approchée par une somme :

I
k
	​

=I
k−1
	​

+e
k
	​

Δt

I
k
	​

=I
k−1
	​

+e
k
	​

Δt

Dérivée

Approchée par différence finie :

D
k
	​

=
Δt
e
k
	​

−e
k−1
	​

	​


D
k
	​

=
Δt
e
k
	​

−e
k−1
	​

	​


PID discret

Donc :

u
k
	​

=K
p
	​

e
k
	​

+K
i
	​

I
k
	​

+K
d
	​

D
k
	​


u
k
	​

=K
p
	​

e
k
	​

+K
i
	​

I
k
	​

+K
d
	​

D
k
	​


5. Simulation du système

Ensuite PIDToolBox met à jour le système.

Euler explicite (simple)

Pour :

x
˙
=f(x,u)

on approxime :

x
k+1
	​

=x
k
	​

+Δtf(x
k
	​

,u
k
	​

)

x
k+1
	​

=x
k
	​

+Δtf(x
k
	​

,u
k
	​

)

C’est la méthode numérique la plus basique.

6. Exemple ultra concret

Prenons :

G(s)=
s+1
1
	​


Équation temporelle :

y
˙
	​

=−y+u

Avec Euler :

y
k+1
	​

=y
k
	​

+Δt(−y
k
	​

+u
k
	​

)

y
k+1
	​

=y
k
	​

+Δt(−y
k
	​

+u
k
	​

)

7. Ce que fait le logiciel à CHAQUE pas

Boucle :

consigne r
↓
calcul erreur e
↓
calcul PID u
↓
mise à jour système
↓
nouvelle sortie y
↓
stockage point de courbe
↓
pas suivant

La courbe finale est juste :

(t
k
	​

, y
k
	​

)

pour tous les instants simulés.

8. Pseudo-code exact

Voici quasiment ce qu’un PIDToolBox interne fait :

dt = 0.001

y = 0
integral = 0
prev_error = 0

for k in range(N):

    t = k * dt

    # consigne step
    r = 1

    # erreur
    error = r - y

    # PID
    integral += error * dt

    derivative = (error - prev_error) / dt

    u = Kp * error + Ki * integral + Kd * derivative

    prev_error = error

    # système : y' = -y + u
    dy = -y + u

    # Euler
    y = y + dt * dy

    # stocker point
    save(t, y)
9. Pourquoi ça marche ?

Parce qu’avec un petit :

Δt

la dérivée :

dt
dy
	​


est bien approximée par :

Δt
y
k+1
	​

−y
k
	​

	​

10. Méthodes plus avancées utilisées dans les vrais logiciels

PIDToolBox peut utiliser :

Euler
Runge-Kutta 4 (RK4)
Bilinear/Tustin
Zero-order hold
solveurs ODE

car Euler simple devient imprécis si :

système rapide
oscillations
forte raideur
11. RK4 (beaucoup plus précis)

Au lieu de :

x
k+1
	​

=x
k
	​

+Δtf(x
k
	​

)

RK4 calcule plusieurs pentes intermédiaires :

k
1
	​

,k
2
	​

,k
3
	​

,k
4
	​


puis moyenne.

C’est souvent ce qu’utilisent Matlab/Simulink/Scipy.

12. Dans MATLAB / Python control

Quand tu fais :

control.step_response(sys)

la bibliothèque :

transforme le système en espace d’état
choisit un pas temporel
résout l’ODE numériquement
renvoie :
t
y(t)

exactement comme PIDToolBox.

13. Résumé ultra court

PIDToolBox :

1.

discrétise le temps

t
k
	​

=kΔt
2.

calcule PID :

u
k
	​

3.

met à jour le système :

x
k+1
	​

4.

stocke :

(t
k
	​

,y
k
	​

)
5.

trace la courbe.

on utilise welch ici  ?

Pas directement pour une step response classique.

La méthode de Welch's method (méthode de Welch) sert surtout à estimer une densité spectrale de puissance (PSD), donc à analyser les fréquences d’un signal bruité.

Elle est utilisée pour :

analyse fréquentielle
vibrations
bruit
FFT améliorée
identification système
estimation de bande passante

Mais une réponse indicielle PID est principalement une simulation temporelle.

1. Ce que fait Welch

Welch calcule :

P
xx
	​

(f)

la puissance du signal selon la fréquence.

Le principe :

découper le signal en fenêtres
FFT sur chaque fenêtre
moyenner les spectres

Formellement :

P
xx
	​

(f)=
K
1
	​

k=1
∑
K
	​

∣X
k
	​

(f)∣
2

P
xx
	​

(f)=
K
1
	​

∑
k=1
K
	​

∣X
k
	​

(f)∣
2

2. Pourquoi ce n’est pas utilisé pour la step response

Une step response cherche :

y(t)

dans le temps.

Donc on résout :

x
˙
=f(x,u)

numériquement.

Pas besoin de FFT.

3. Où Welch PEUT intervenir dans un outil PID

Par contre PIDToolBox ou des outils similaires peuvent utiliser Welch pour :

Analyse du bruit capteur

Exemple :

capteur vitesse
vibration
bruit mesure

On veut connaître :

fréquence dominante
bruit haute fréquence
résonance
Identification fréquentielle

Si on injecte un signal bruité/chirp/PRBS :

entrée u(t)
sortie y(t)

Welch peut estimer :

gain fréquentiel
résonances
Bode expérimental
4. Relation avec PID

Le terme dérivé :

K
d
	​

dt
de
	​


amplifie énormément le bruit haute fréquence.

Donc Welch peut aider à :

détecter bruit HF
choisir filtre dérivé
régler cutoff
5. Exemple concret

Supposons une mesure :

y(t) = vraie_vitesse + bruit

Tu appliques Welch :

from scipy.signal import welch

f, Pxx = welch(signal, fs=1000)

Tu obtiens :

fréquence dominante
spectre bruit
6. Comparaison claire
Step response

Analyse :

temporelle

Axes :

X = temps
Y = sortie

Utilise :

ODE
Euler
RK4
Welch

Analyse :

fréquentielle

Axes :

X = fréquence
Y = puissance

Utilise :

FFT
moyennage spectral
7. En contrôle avancé

Welch est souvent utilisé avec :

identification système
contrôle robuste
analyse vibration
tuning industriel

mais pas pour calculer directement :

overshoot
rise time
settling time
8. Ce qu’utilise probablement PIDToolBox

Pour le graphe step response :

✅ simulation numérique temporelle

Possiblement :

Euler
RK4
solveur ODE

Pour des outils annexes :

✅ Welch possible pour :

FFT
bruit
analyse fréquentielle
PSD
diagnostic oscillation PID