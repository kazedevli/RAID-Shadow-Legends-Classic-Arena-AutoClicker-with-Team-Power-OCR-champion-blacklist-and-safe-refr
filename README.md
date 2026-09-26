# RAID Arena AutoClicker

AutoClicker napravljen za automatsko igranje Classic Arene u RAID: Shadow Legends.

## Requirements

- Windows 10 ili Windows 11
- Python 3.10+ (preporučeno)
- Tesseract OCR
- RAID: Shadow Legends PC verzija
- Rezolucija/prozor igre podešen prema zahtevima aplikacije

---

## Installation

### 1. Install Python

Preuzmite i instalirajte Python 3.

Tokom instalacije OBAVEZNO uključite:

Add Python to PATH

Provera instalacije:

python --version

Ako komanda prikazuje verziju Pythona, instalacija je uspešna.

---

### 2. Install Tesseract OCR

Program koristi Tesseract za čitanje Team Power vrednosti sa ekrana.

Instalirajte Tesseract OCR za Windows.

Podrazumevane lokacije koje AutoClicker automatski proverava uključuju:

C:\Program Files\Tesseract-OCR\tesseract.exe

C:\Program Files (x86)\Tesseract-OCR\tesseract.exe

Ako Tesseract nije instaliran, AutoClicker neće moći pravilno da čita Team Power.

---

### 3. Download the project

Na GitHub stranici izaberite:

Code → Download ZIP

Raspakujte kompletan ZIP u jedan folder.

VAŽNO:

Nemojte pokretati skriptu direktno iz ZIP arhive.

Svi `.py`, `.png`, `.bat` i ostali fajlovi moraju ostati zajedno u odgovarajućoj strukturi projekta.

---

### 4. Install Python dependencies

Otvorite Command Prompt u folderu AutoClickera i pokrenite:

pip install -r requirements.txt

Ako `pip` komanda ne radi:

python -m pip install -r requirements.txt

Potrebne biblioteke uključuju komponente za:

- OpenCV
- PyAutoGUI
- Tesseract/Pytesseract
- NumPy
- obradu slike i automatizaciju

---

### 5. Automatic installation

Ako projekat sadrži `INSTALL.bat`, možete umesto ručne instalacije Python biblioteka pokrenuti:

INSTALL.bat

Preporučeno je:

Right Click → Run as Administrator

Sačekajte da instalacija završi.

---

## Starting the AutoClicker

### 1. Pokrenite RAID: Shadow Legends

Prvo otvorite RAID i uđite u:

Battle → Arena → Classic Arena

Lista protivnika mora biti vidljiva.

---

### 2. Podesite RAID prozor

AutoClicker koristi screenshotove i prepoznavanje elemenata ekrana.

Zbog toga RAID prozor mora biti:

- vidljiv
- na glavnom monitoru
- pravilno pozicioniran
- odgovarajuće veličine

Nemojte prekrivati RAID drugim prozorima dok AutoClicker radi.

AutoClicker koristi miš računara, tako da se računar ne preporučuje za druge aktivnosti dok bot aktivno igra Arenu.

---

### 3. Start AutoClickera

Pokrenite:

START.bat

ili ručno:

python raid_arena_auto.py

Otvoriće se AutoClicker aplikacija.

---

## Configuration

Pre pokretanja podesite željene opcije.

### Team Power Limit

Određuje maksimalni Team Power protivnika kojeg AutoClicker sme da napadne.

Primer:

500K

AutoClicker će tražiti protivnike ispod postavljenog limita i izabrati odgovarajućeg kandidata.

Team Power podržava vrednosti kao:

260K  
556.38K  
25,253  
79,147  
1.27M

---

### Champion Blacklist

Blacklist omogućava preskakanje određenih protivničkih heroja/timova.

Ako AutoClicker prepozna aktiviranog blacklist heroja, taj protivnik se preskače čak i ako je Team Power ispod dozvoljenog limita.

---

### GEM Use

Ako je GEM Use ISKLJUČEN:

AutoClicker NE SME trošiti Gems za Arena refill.

Ako se pojavi GEM refill prozor, AutoClicker izlazi iz njega.

Ako je GEM Use UKLJUČEN:

AutoClicker može koristiti dozvoljeni GEM refill.

PAŽNJA:

Uključivanjem ove opcije dozvoljavate programu da troši Gems.

---

### FREE Refresh

AutoClicker koristi Arena Refresh samo kada prepozna da je Refresh FREE.

Ako postoje dozvoljeni protivnici, prvo će pokušati da odigra njih.

FREE Refresh se koristi kada nema odgovarajućeg protivnika.

Refresh koji zahteva Gems ne bi trebalo da bude korišćen.

---

## Starting Arena Farming

Kada je sve podešeno:

1. Otvorite Classic Arena listu protivnika.
2. Proverite Team Power Limit.
3. Proverite Champion Blacklist.
4. Proverite GEM Use opciju.
5. Kliknite START u AutoClickeru.

Program će automatski:

- pronaći Battle redove
- pročitati Team Power
- proveriti blacklist
- pronaći odgovarajućeg protivnika
- pokrenuti Battle
- pokrenuti borbu
- detektovati rezultat
- vratiti se u Arena meni
- nastaviti sledeću borbu
- koristiti FREE Refresh kada je dozvoljen
- obraditi Arena refill situacije

---

## Important

AutoClicker zavisi od vizuelnog prepoznavanja elemenata igre.

Promena:

- RAID interfejsa
- rezolucije
- UI scale-a
- izgleda dugmadi
- Arena menija

može uticati na prepoznavanje.

Ako program počne pogrešno da prepoznaje elemente, zaustavite ga pre nastavka.

---

## Emergency Stop

Ako AutoClicker radi nešto neočekivano:

STOPIRAJTE AutoClicker.

Nemojte ostavljati program bez nadzora dok prvo ne proverite da pravilno radi sa vašim podešavanjima.

---

## Error Screenshots

Ako AutoClicker detektuje određene greške, može automatski sačuvati screenshot problema.

Screenshotovi se nalaze u:

error_screenshots/

Oni mogu pomoći pri dijagnostikovanju problema.

---

## Disclaimer

Ovaj projekat je nezavisan community alat i nije povezan sa Plariumom ili RAID: Shadow Legends.

Korišćenje automatizacije u online igrama može biti protivno pravilima ili Terms of Service igre.

Koristite program na sopstvenu odgovornost.
