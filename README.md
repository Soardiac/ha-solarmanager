# Solarmanager – Home Assistant (Custom Integration)
**--> Inoffiziell: Die Firma Solar Manager ist für diesen Code <ins>nicht</ins> verantwortlich.**
#
Stellt alle Felder für alle Geräte aus der API in HA zur Verfügung.
Verwendet die Cloud-API von Solar Manager. Du brauchst einen Account dort.
API Doku: https://external-web.solar-manager.ch/swagger

Die verschiedenen Betriebsmodi können gesetzt werden. Die Modi verwenden je unterschiedliche Parameter (zB Ladegrenzen etc.), so ist das in Solarmanager gelöst. In HA sind diese verschiedenen Parameter alle sichtbar. Die Werte greifen aber nur, wenn der entsprechende Modus gesetzt ist.

## Installation
**HACS (empfohlen)**
1. HACS → Integrations → `+` → **Custom repositories** → URL dieses Repos, Kategorie *Integration*.
2. „Solarmanager“ suchen, installieren, Home Assistant neu starten.

**Manuell**
- Ordner `custom_components/solarmanager` nach `<config>/custom_components/` kopieren, HA neu starten.

## Einrichtung
- Einstellungen → Geräte & Dienste → Integration hinzufügen → **Solarmanager**
- E-Mail, Passwort, **Solar Manager ID (smId)** eintragen.

## Issues
- Code ist für mich geschrieben, es gibt viele Dinge, die für "ein Produkt" fehlen.
- Robusteres Refresh/Login
- Übersetzungen
- QA (Testing, Linting, Tools,...)
