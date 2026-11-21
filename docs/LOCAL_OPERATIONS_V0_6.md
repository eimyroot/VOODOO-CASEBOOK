# VOODOO-CASEBOOK v0.6 — bezpečný ruční lokální provoz

**Stav:** izolovaný zdrojový kandidát. Bez produkčního nasazení, placených služeb, agenta, daemonu a síťového API.

## Projektové hranice

- Výchozí main: 38457b11f100a550da709a4cfcfd3c80306690ff.
- Skutečná data, zálohy, obnovy a evidence zůstávají mimo 0_DEV a mimo Git.
- Složky 0700, soubory 0600. Tyto režimy NEIZOLUJÍ procesy pod stejným uživatelským účtem. Bez samostatné identity nebo důsledně omezených oprávnění nelze garantovat ochranu před všemi lokálními agenty.
- CASEBOOK nedostává žádný nový CASER provider WRITE, synchronizaci, automatické spouštění, přístupový token ani datový grant.
- Výstup obnovení není pokynem k aktivaci.

## A. Cross-process cold start

- Při inicializaci se používá stabilní soubor .casebook-init.lock a POSIX flock.
- Zámek chrání i samotné první vytvoření databáze; soubor se otevírá již s režimem 0600.
- Každý zapisovač dokončí krátkou inicializační sekci před BEGIN IMMEDIATE a plnou kontrolou historie.
- Čekání na lock má limit přibližně deset sekund, pak operace bezpečně selže.
- NEMAŽTE .casebook-init.lock, dokud mohou být aktivní procesy. Jeho odstranění může způsobit rozdělení zámku mezi dvě různé inode.
- Přerušené vytvoření schématu není automaticky opravováno. Neúplná DB musí zůstat BLOCKED, dokud operátor nerozhodne o obnově.

## B. Ruční zálohování a obnova

Zkontrolujte zdrojovou verzi a cílovou cestu. Toto je postup pro operátora, nikoli spuštěná automatizace. **Dokud v0.6 není schválená a lokální checkout skutečně obsahuje její commit, nespouštějte níže uvedené příkazy proti živé databázi.**

    cd /Users/eimyna/0_DEV/VOODOO-CASEBOOK
    export VOODOO_CASEBOOK_DATA_ROOT=/Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK
    python3 -B -m voodoo_casebook verify
    python3 -B -m voodoo_casebook backup
    python3 -B -m voodoo_casebook backup-verify --backup-id PRESNE_ID_Z_VYSTUPU

Obnovu vždy provést do nové složky mimo aktivní data-root a mimo 0_DEV. Rodič musí existovat a mít oprávnění 0700. Následující mkdir je jen ukázka pro přípravu cíle, nespouští se automaticky:

    mkdir -m 700 /Users/eimyna/0_EVIDENCE/CASEBOOK-RESTORE-LAB

Nová složka restore-001 ještě NESMÍ existovat:

    python3 -B -m voodoo_casebook --data-root /Users/eimyna/0_EVIDENCE/VOODOO-CASEBOOK restore-to-new-root --backup-id PRESNE_ID_Z_VYSTUPU --to /Users/eimyna/0_EVIDENCE/CASEBOOK-RESTORE-LAB/restore-001

    python3 -B -m voodoo_casebook --data-root /Users/eimyna/0_EVIDENCE/CASEBOOK-RESTORE-LAB/restore-001 verify

Postup je striktně nedestruktivní:

1. Nejprve ověřit uložený manifest, SHA-256 snapshotu, schéma, triggery a celý hashový řetězec.
2. Kopie se vytváří jen do nové privátní složky; přepis existujícího nebo živého úložiště je odmítnut.
3. Po kopii ověřit SHA-256 a znovu celé SQLite, počty událostí a session a hash hlavy.
4. V případě chyby odstranit jen nově vytvořenou cílovou složku; aktivní data neměnit.
5. Stav RESTORE_VERIFIED_ISOLATED dokládá obnovení jedné konkrétní lokální zálohy; nikdy automatickou aktivaci.

Živou SQLite databázi ve WAL režimu nekopírovat prostým příkazem cp. Existující backup používá konzistentní online SQLite snapshot.

## C. Stavové rozhodování

| Stav | Bezpečný krok |
| --- | --- |
| Integrita OK | Ruční ověření, záloha, ověření konkrétního backup ID |
| Zvýšené náklady na opakované zápisy | V autorizovaném lokálním kódu používat existující opt-in dávky 1–32 |
| Lock timeout | Ověřit stav; případný retry se stejným session/event ID |
| Chybějící trigger nebo poškozený řetězec | Zastavit zápis a chránit důkazy; nikdy automaticky opravovat |
| Obnova | Pouze oddělená prázdná složka a druhá úplná kontrola |
| Ztracený Mac nebo klíč | Lokální snapshot není off-device disaster recovery |

## D. Otevřené provozní podmínky

- Šifrovaný off-device backup + nezávislé otestování klíče a obnovení na jiném zařízení: NEOVĚŘENO.
- Externí nezávislá kryptografická kotva hlavy a podpis verifieru: NENÍ.
- Izolace všech procesů běžících pod stejným macOS účtem: NENÍ.
- 10k/100k událostí, produkční SLO, observabilita, RTO/RPO: NENÍ OVĚŘENO.
- Automatická rotace, přepnutí na zálohu a nezávislé nasazení: NEAKTIVNÍ.
- Žádná registrace k CASER, spuštění agentů, CI tajemství, síťové porty nebo změna produkčního rootu.

## Release gate

Review přesného SHA, GitHub CI, explicitní source merge a následné samostatné rozhodnutí pro případnou ruční lokální aktivaci. Úspěch testů není ekvivalentem provozní připravenosti.
