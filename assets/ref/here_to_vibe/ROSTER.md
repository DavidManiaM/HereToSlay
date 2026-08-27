# Here to Vibe (Code) — roster

Source of truth for names, classes, and which original card each maps to.
Decisions: [`QUESTIONS.md`](QUESTIONS.md). Glossary: [`GLOSSARY.md`](GLOSSARY.md).
People: [`PEOPLE.md`](PEOPLE.md).

**Status:** mapping locked 2026-08-27. Art started (5 besties + 6 persoane; E-DIE needs redo).

Legend: `ok` = same mechanic as original · `changed` = different effect · `new` = not in base · `1×` = one copy (not the original 2×)

---

## Șefii grupurilor (Party Leaders)

| Original | Id | Vibe | Class | Ability (your text) | Map |
|----------|----|------|-------|---------------------|-----|
| The Cloaked Sage | `base.leader.cloaked_sage` | **Claude Max** | Magician | Când joci un script, mai tragi o barfă | ok — art generated |
| The Shadow Claw | `base.leader.shadow_claw` | **Enemy_key.pem** | Hoț | O dată per rundă, pe tura ta, un prompt ca să furi o barfă | ok — art generated |
| The Protecting Horn | `base.leader.protecting_horn` | **Digi** | Gardian | +1 sau −1 download/upload speed la fiecare modificator | ok — art generated |
| The Fist of Reason | `base.leader.fist_of_reason` | **Random.org** | Luptător | +2 la fiecare np.random() | changed — locked: all rolls, not only Șia all in — art generated |
| The Divine Arrow | `base.leader.divine_arrow` | **Puterea prieteniei** | Arcaș | +1 np.random() când încerci o împrietenire | ok — art generated |
| The Charismatic Song | `base.leader.charismatic_song` | **Suc de portocale** | Cântăreț | +1 np.random() când activezi puterea unei persoane | ok — art generated |

---

## Besties (Monsters) — class + persoană

| Original | Id | Vibe | Need | Bands | On-friend skill | Map |
|----------|----|------|------|-------|-----------------|-----|
| Orthus | `base.monster.orthus` | **Cristina „Insta-Cast”** | Magician + persoană | 8+ / 5–7 / 4− discard 2 | Poți juca orice Script imediat după ce îl tragi | ok — art generated |
| Dark Dragon King | `base.monster.dark_dragon_king` | **Vl-ADD** | Cântăreț + persoană | 8+ / 5–7 / 4− sacrifici o persoană | +1 np.random() la activarea unei persoane | ok — art generated (Vlad) |
| Rex Major | `base.monster.rex_major` | **Andrei „1+1 Gratis”** | Gardian + persoană | 8+ / 5–7 / 4− discard 2 | Când joci o persoană, mai tragi o barfă | ok — art generated |
| Titan Wyvern | `base.monster.titan_wyvern` | **Mihai „RNG-esus”** | Luptător + persoană | 8+ / 5–7 / 4− discard 2 | După ce joci o persoană, +1 la următorul np.random() din tură | ok — art generated |
| Crowned Serpent | `base.monster.crowned_serpent` | **Cupi-Dan** | Hoț + persoană | 10+ / 7–9 / 6− sacrifici | +1 la împrieteniri | ok — art generated |
| Warworn Owlbear | `base.monster.warworn_owlbear` | **Adren-Alina** | Arcaș + persoană | 8+ / 5–7 / 4− discard 2 | Tragi extra când tragi în afara turei | ok vs printed — art generated |

## Besties — only persoane

| Original | Id | Vibe | Need | Bands | On-friend skill | Map |
|----------|----|------|------|-------|-----------------|-----|
| Mega Slime | `base.monster.mega_slime` | **SiemensGPT** | 4 persoane | 8+ / (no safe) / 7− sacrifici | +1 prompt în fiecare tura ta | ok — art generated |
| Abyss Queen | `base.monster.abyss_queen` | **Paul-tergeist** | 3 persoane | 8+ / 6–7 / 5− sacrifici | +1 MB/s când un oponent aplică download/upload **pe aruncarea ta** | ok — art generated |
| Anuran Cauldron | `base.monster.anuran_cauldron` | **Evelina „Perma-Buff”** | 3 persoane | 7+ / (no safe) / 6− sacrifici | +1 la fiecare np.random() | ok — art generated |
| Corrupted Sabretooth | `base.monster.corrupted_sabretooth` | **Cristi Iri-MAI-ia** | 3 persoane | 9+ / 7–8 / 6− sacrifici | La o împrietenire reușită, mai tragi o barfă | ok — art generated |
| Bloodwing | `base.monster.bloodwing` | **E-DIE** | 2 persoane | 9+ / 7–8 / 6− sacrifici | Cine te provoacă trebuie să arunce o barfă | needs-redo — last photo was Vlad |
| Arctic Aries | `base.monster.arctic_aries` | **Alexan-DRAW** | 1 persoană | 10+ / 7–9 / 6− sacrifici | La activare reușită, tragi o barfă | ok — art generated |
| Terratuga | `base.monster.terratuga` | **Andrei Dictator** | 2 persoane | 11+ / 7–10 / 6− sacrifici | Persoanele tale nu mai pot fi distruse | ok — art generated |
| Malamammoth | `base.monster.malamammoth` | **Silviu „Gamemode 1”** | 2 persoane | 9+ / 6–8 / 5− discard 2 | Când joci un cheat sau hack, mai tragi o barfă | ok — art generated |
| Dracos | `base.monster.dracos` | **Re-VIO** | 2 persoane | 9+ / 6–8 / 5− sacrifici | Poți rerolla np.random() la activarea unei persoane | ok — art generated |

---

## Scripturi (Magic) — 2 copii fiecare

| Original | Id | Vibe | Ability | Map |
|----------|----|------|---------|-----|
| Destructive Spell | `base.magic.destructive_spell` | **~ClassDestructor** | Înlătură o barfă, apoi distruge o persoană | ok — art generated |
| Call to the Fallen | `base.magic.call_to_the_fallen` | **SearchHistory** | Caută în aruncări o persoană, ia-o în prompturi | ok — art generated |
| Forced Exchange | `base.magic.forced_exchange` | **Swap** | Fură o persoană, apoi dă una din grupul tău | ok — art generated |
| Enchanted Spell | `base.magic.enchanted_spell` | **NotSoRandom.org** | +2 la toate np.random() până la sfârșitul turului | ok — art generated |
| Winds of Change | `base.magic.winds_of_change` | **Microsoft Defender** | Returnează un cheat/hack echipat, apoi trage | ok — art generated |
| Forceful Winds | `base.magic.forceful_winds` | **BitDefender** | Returnează toate obiectele echipate | ok — art generated |
| Critical Boost | `base.magic.critical_boost` | **GiveMeBabyOneMoreCard** | Trage 3, apoi înlătură 1 | ok — art generated |
| Entangling Trap | `base.magic.entangling_trap` | **SegmentationFault** | Înlătură 2, apoi fură o persoană | ok — art generated |

---

## Cheat-uri și hack-uri (12 unice, 1 copie fiecare)

Original: 6 kinds × 2 copies. Here: the 12 names below, **one copy each**.

### Cheat-uri (pe persoanele tale)

| Original | Id / slug | Vibe | Ability | Map |
|----------|-----------|------|---------|-----|
| Really Big Ring | `base.item.really_big_ring` | **+2 Noroc Buff** | +2 np.random() la abilitatea echipată | ok, 1× — art generated |
| Particularly Rusty Coin | `base.item.particularly_rusty_coin` | **Am mai dat, dar am și luat** | Dacă ratezi aruncarea, tragi o barfă | ok, 1× — art generated |
| Decoy Doll | `base.item.decoy_doll` | **EC2 image** | Dacă persoana ar fi sacrificată/distrusă, arunci acest cheat în loc | ok, 1× — art generated |
| — | `tortul_de_la_siemens` | **Tortul de la Siemens** | La activare, abilitatea se joacă de 2 ori | new, 1× — art generated |
| — | `rota_lorifer` | **Rota-lorifer** | Persoana nu mai poate fi înghețată | new, 1× — art generated |
| — | `cafelutza_puternicoasa` | **Cafelutza puternicoasă** | La **începutul turei tale**, înainte de orice prompt, abilitatea persoanei echipate se declanșează singură (fără np.random(), fără cost) | new, 1× — art generated |

### Hack-uri (pe persoanele oponentului)

| Original | Id / slug | Vibe | Ability | Map |
|----------|-----------|------|---------|-----|
| Suspiciously Shiny Coin | `base.item.suspiciously_shiny_coin` | **Nu ai voie să ai cărți!** | Dacă reușești aruncarea, înlătură o barfă | ok, 1× — art generated |
| Curse of the Snake's Eyes | `base.item.curse_of_the_snakes_eyes` | **GHINION! (Gina Felea)** | −2 np.random() la abilitatea echipată | ok, 1× — art generated |
| Sealing Key | `base.item.sealing_key` | **Error 404** | Nu poți folosi efectul persoanei | ok, 1× — art generated |
| — | `yawn` | **Yawn** | Abilitatea costă 2 tokeni în loc de 1 | new, 1× — art generated |
| — | `aerul_conditionat` | **Aerul condiționat** | Persoana e înghețată: nu-i poți activa abilitatea | new, 1× — art generated |
| — | `ciorba_de_salata` | **Ciorba de salată** | Abilitatea cere 11+ oricum | new, 1× — art generated |

## Măști (class-change)

**Six extra cards** in the main deck (not replacing cheats/hacks). Equipped persoană counts as the printed class instead of its own.

| Slug | Name | Class it maps to | Text to print | Map |
|------|------|------------------|---------------|-----|
| `mask_0` | **/0** | Magician | Persoana echipată e considerată **Magician**. | art generated |
| `mask_6` | **/6** | Hoț | Persoana echipată e considerată **Hoț**. | art generated |
| `mask_12` | **/12** | Gardian | Persoana echipată e considerată **Gardian**. | art generated |
| `mask_18` | **/18** | Luptător | Persoana echipată e considerată **Luptător**. | art generated |
| `mask_24` | **/24** | Arcaș | Persoana echipată e considerată **Arcaș**. | art generated |
| `mask_30` | **/30** | Cântăreț | Persoana echipată e considerată **Cântăreț**. | art generated |

## Download/upload speed (Modifiers)

| Original | Copies | Romanian name | Map |
|----------|--------|---------------|-----|
| +1 / −3 | 8 | **Plus unu / minus trei** | art generated |
| +2 / −2 | 8 | **Plus doi / minus doi** | art generated |
| +3 / −1 | 8 | **Plus trei / minus unu** | art generated |
| +4 / −4 | 8 | **Plus patru / minus patru** | art generated |

## Șia all in (Challenge)

| Original | Copies | Vibe | Map |
|----------|--------|------|-----|
| Challenge | 7 | **Șia all in** | art generated |

---

## Persoane — Cântăreț (Bard)

| Original | Id | Roll | Vibe | Ability / note | Map |
|----------|----|------|------|----------------|-----|
| Dodgy Dealer | `base.hero.dodgy_dealer` | 9+ | **Cătă-LINK** | Schimbă prompturile cu alt jucător | ok — art generated |
| Fuzzy Cheeks | `base.hero.fuzzy_cheeks` | 8+ | **Florin „Drop-in”** | Trage o barfă și joacă imediat o persoană | ok — art generated |
| Greedy Cheeks | `base.hero.greedy_cheeks` | 8+ | **David „Sub-Zero”** | Fiecare alt jucător îți dă o barfă (bani pentru criogenare) | ok — art generated |
| Lucky Bucky | `base.hero.lucky_bucky` | 7+ | **Lăutarul Matei „Ciordales”** | Trage din mâna cuiva; dacă e persoană, o poți juca imediat | ok — art generated |
| Mellow Dee | `base.hero.mellow_dee` | 7+ | **Cezar „Topdeck”** | Trage; dacă e persoană, o poți juca (barfe despre Teo) | ok — art generated |
| Napping Nibbles | `base.hero.napping_nibbles` | any | **Cartela de la ușă** | Nu face nimic; reușește oricum | ok — art generated (object) |
| Peanut | `base.hero.peanut` | 7+ | **MaestrulMania** | Trage 2 | ok — art generated |
| Tipsy Tootie | `base.hero.tipsy_tootie` | 6+ | **Cezarinho Hotomanul** | Fură o persoană, apoi mută cartea asta la el | ok — art generated |

## Persoane — Luptător (Fighter)

| Original | Id | Roll | Vibe | Ability / note | Map |
|----------|----|------|------|----------------|-----|
| Bad Axe | `base.hero.bad_axe` | 8+ | **TUDOR-minatorul** | Distruge o persoană | ok — art generated |
| Bear Claw | `base.hero.bear_claw` | 7+ | **Sebi Șoimul** | Trage din mână; dacă e persoană, încă una | ok — art generated |
| Beary Wise | `base.hero.beary_wise` | 7+ | **Răzvan The Disarmer** | Toți înlătură una; alegi una în prompturi | ok — art generated |
| Fury Knuckle | `base.hero.fury_knuckle` | 5+ | **Provoca-Rota** | Trage; dacă e confruntare, încă una | ok — art generated |
| Heavy Bear | `base.hero.heavy_bear` | 5+ | **Rebek-atac** | Alege un jucător; ăla înlătură 2 | ok — art generated |
| Pan Chucks | `base.hero.pan_chucks` | 8+ | **AlEXTRACT** | Trage 2; dacă e confruntare, poți distruge o persoană | ok — art generated |
| Qi Bear | `base.hero.qi_bear` | 10+ | **Larisa „Wipeout”** | Înlătură până la 3; pentru fiecare, distruge o persoană | ok — art generated |
| Tough Teddy | `base.hero.tough_teddy` | 4+ | **Raul-ambo** | Fiecare cu un Luptător în grup înlătură o barfă | ok — art generated |

## Persoane — Gardian

| Original | Id | Roll | Vibe | Ability / note | Map |
|----------|----|------|------|----------------|-----|
| Calming Voice | `base.hero.calming_voice` | 9+ | **Sebi și Omul** | Persoanele tale nu pot fi furate până la tura ta | ok — art generated |
| Guiding Light | `base.hero.guiding_light` | 7+ | **Ralu-CAUTĂ** | Caută în aruncări o persoană | ok — art generated |
| Holy Curselifter | `base.hero.holy_curselifter` | 5+ | **Ius-Tazer** | Returnează un hack de pe o persoană a ta | ok — art generated |
| Iron Resolve | `base.hero.iron_resolve` | 8+ | **Dino Adorabilul** | Cărțile tale nu pot fi contestate restul turului (prea drăguț) | ok — art generated |
| Mighty Blade | `base.hero.mighty_blade` | 8+ | **David the Maniac** | Persoanele tale nu pot fi distruse până la tura ta | ok — art generated |
| Radiant Horn | `base.hero.radiant_horn` | 6+ | **Ale-X-tra** | Caută în aruncări un modificator (notă: Camile) | ok — art generated |
| Vibrant Glow | `base.hero.vibrant_glow` | 9+ | **Răz-One Man Army** | +5 la toate np.random() până la sfârșitul turului | ok — art generated |
| Wise Shield | `base.hero.wise_shield` | 6+ | **Matrei** | +3 la toate np.random() până la sfârșitul turului | ok — art generated |

## Persoane — Arcaș (Ranger)

| Original | Id | Roll | Vibe | Ability / note | Map |
|----------|----|------|------|----------------|-----|
| Bullseye | `base.hero.bullseye` | 7+ | **Iust-INspectorul** | Primele 3 din pachet, iei una (ciorbă–carne–garnitură) | ok — art generated |
| Hook | `base.hero.hook` | 6+ | **Răzvan Hood** | Joacă imediat un cheat/hack din mână și trage | ok — art generated |
| Lookie Rookie | `base.hero.lookie_rookie` | 5+ | **Ranger Răzvan** | Caută în aruncări un obiect | ok |
| Quick Draw | `base.hero.quick_draw` | 8+ | **Roberta Turnu Severin** | Trage 2; dacă e obiect, poți juca unul imediat | ok — art generated |
| Serious Grey | `base.hero.serious_grey` | 9+ | **Răzvanish from the Board** | Distruge o persoană și trage | ok — art generated |
| Sharp Fox | `base.hero.sharp_fox` | 5+ | **S-a ZIS-u cu tine** | Te uiți la mâna unui jucător | ok — art generated |
| Wildshot | `base.hero.wildshot` | 8+ | **Mari-A-rrow** | Trage 3, înlătură 1 | ok — art generated |
| Wily Red | `base.hero.wily_red` | 7+ | **Cezarinho** | Trage 2, apoi înlătură 1 | ok — art generated |

## Persoane — Hoț (Thief)

| Original | Id | Roll | Vibe | Ability / note | Map |
|----------|----|------|------|----------------|-----|
| Kit Napper | `base.hero.kit_napper` | 9+ | **Rob-erta** | Fură o persoană | ok — art generated |
| Meowzio | `base.hero.meowzio` | 10+ | **AleX-TREME** | Fură o persoană și trage o barfă din mâna lui | ok — art generated |
| Plundering Puma | `base.hero.plundering_puma` | 6+ | **TRAGOȘ** | Trage 2 din mână; ăla poate trage una | ok — art generated |
| Shurikitty | `base.hero.shurikitty` | 9+ | **Raul-ing in the Deep** | Distruge; dacă avea obiect, îl iei tu | ok — art generated |
| Silent Shadow | `base.hero.silent_shadow` | 8+ | **Sebi-Servire** | Te uiți la mână și iei o barfă (înghețată) | ok — art generated |
| Slippery Paws | `base.hero.slippery_paws` | 6+ | **MaRIZZZ** | Trage 2 din mâna cuiva, apoi înlătură una (trage somn și-l aruncă) | ok — art generated |
| Sly Pickings | `base.hero.sly_pickings` | 6+ | **Răz-VAN-illa** | Trage; dacă e obiect, îl joci (tot înghețată) | ok — art generated |
| Smooth Mimimeow | `base.hero.smooth_mimimeow` | 7+ | **Te-ai La-RISC-at** | Trage câte una de la fiecare care are un Hoț | ok — art generated |

## Persoane — Magician (Wizard)

| Original | Id | Roll | Vibe | Ability / note | Map |
|----------|----|------|------|----------------|-----|
| Bun Bun | `base.hero.bun_bun` | 5+ | **MariaDB** | Caută în aruncări un script | ok — art generated |
| Buttons | `base.hero.buttons` | 6+ | **Ciord-Zisu** | Trage o barfă din mâna cuiva | ok — art generated |
| Fluffy | `base.hero.fluffy` | 10+ | **Mincu-cid** | Distruge 2 persoane | ok — art generated |
| Hopper | `base.hero.hopper` | 7+ | **La-RISK-a tot** | Alege un jucător; ăla sacrifică o persoană | ok — art generated |
| Snowball | `base.hero.snowball` | 8+ | **Ab-Raul-cadabra** | Trage câte una pentru fiecare Magician din grup | ok — art generated |
| Spooky | `base.hero.spooky` | 10+ | **Ce-Zar** | Alege un jucător; norocul lui e plafonat la 6 (un singur zar) **până i se termină tura** | changed — art generated |
| Whiskers | `base.hero.whiskers` | 11+ | **Răzvandalf the Wizard** | Fură o persoană și distruge o persoană | ok — art generated |
| Wiggles | `base.hero.wiggles` | 10+ | **Rebek-ability** | Fură o persoană și dai imediat np.random() pe efectul ei | ok — art generated |

---

## File names (when we generate art)

Keep the **original slug** so the engine can resolve art:

`images/by_type/<folder>/<original_slug>.png`

plus a titled copy:

`images/cards/<Vibe_Name_Here_to_Vibe>.png`
