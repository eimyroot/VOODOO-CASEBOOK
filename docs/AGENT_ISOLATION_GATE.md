# CASEBOOK — plán skutečné izolace přístupu agentů (bez aktivace)

**Status:** návrh důvěrové hranice. Nepřidává oprávnění, uživatelské účty, launchd služby ani socket. Projektové README a aktuální macOS vlastnictví mají přednost před obecnou VOODOO architekturou.

## Ověřený výchozí stav

- Soukromá databáze na VoodooBooku je momentálně vlastněná účtem eimyna, její složka má 0700 a soubor 0600.
- To chrání před jinými neprivilegovanými UID, **nikoli** před agenty nebo shellem běžícím jako eimyna.
- CASEBOOK nemá produkční agent broker ani explicitní per-session oprávnění pro CASER; žádný nový grant v0.7 neudělil.
- Neexistuje ověřený doklad, že jiné procesy, které už mohou běžet pod stejným UID, skutečně nemohou přistupovat k těmto bajtům.
- Nikdy neprezentovat filesystem 0600 jako skutečnou agent isolation.

## Požadované oddělení před aktivací

1. Operátor schválí konkrétní zařízení, privátní datový kořen, procesy a role. Samotný přístup k tomuto dokumentu neopravňuje měnit macOS identity.
2. Dokázat nezávislou šifrovanou obnovu na **jiném zařízení** s autentizovaným příjemcem, klíčem dostupným mimo původní Mac a nezávisle ověřeným hashovým řetězcem.
3. Vytvořit oddělenou neadministrátorskou identitu určenou pro CASEBOOK (až po samostatném schválení administrátorských změn), bez interaktivního použití agentů a bez sdílení klíčů.
4. Obnovit kopii do nové soukromé složky pod touto identitou; aktivní databázi eimyna nemazat, neopravovat ani přesouvat bez samostatného rozhodnutí a důkazu rollbacku.
5. Pod skutečným účtem agenta ověřit NEGATIVNÍ test: přímé přečtení 0600 databáze, záloh i soukromého klíče musí skončit EPERM/EACCES. Test musí také prokázat, že agent není administrátor a nemá Full Disk Access ani sudo, které izolaci obchází.
6. Teprve pak uvažovat lokální broker pod vlastníkem CASEBOOK přes Unix socket. Ověřovat skutečné peer UID (na macOS např. getpeereid), ne agentem dodané jméno v payloadu. Vynucovat allowlist metod, explicitní session scope, event type, časovou platnost, idempotentní ID, auditní záznam a možnost revokace.
7. Broker neimplementovat jako obecný shell ani neudělovat přímo cestu k souborům. Oddělit READ od WRITE; každé WRITE v CASER vyžaduje odpovídající schválení a nezávislý výsledek.
8. Pozitivní test: autorizovaná operátorská akce projde pouze přes broker a je auditována. Negativní test: nepovolený UID, session nebo typ události skončí DENIED bez nového záznamu.
9. Aktivace by vyžadovala samostatné schválení konkrétního runtime, záloh, klíčů, monitoring a rollback. Všechny dosud vytvořené PR jsou source-only.

## Co NESMÍ být považováno za hotové

- Nové oprávnění CASER, ASTER, ROOK nebo vzdálenému MCP.
- Same-UID security boundary pomocí chmod 0600.
- Úspěšný test obnovení na stejném Macu jako důkaz off-host DR.
- Ochrana vůči lokálnímu administrátorovi nebo malware s úplnými právy.
- Provozní bezpečnost CMS AES-CBC bez nezávislých certifikátových/digest pinů, nebo bez následné kryptografické revize.

**Source recommendation:** nejprve ukončit kryptografické source-gates, potom off-host key custody a obnovu, poté neadministrátorskou OS izolaci a až nakonec broker. Neschválené změny identit a přístupů jsou BLOCKED.
