import { Card } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";

// Every number and claim here comes from the community wiki the game's
// Discord points newcomers at (tower-hub.com). Where the community disagrees
// with itself, that disagreement is kept rather than papered over. See
// .superpowers/sdd/2026-09-03-menu-shopping/guide-content.md for the
// researched source text this page renders.
const SECTION_HEADING = "text-xs uppercase tracking-wide text-muted-foreground";

function SourceLine({ paths }: { paths: string[] }) {
  return (
    <p className="text-xs text-muted-foreground">
      Source:{" "}
      {paths.map((path, i) => (
        <span key={path}>
          {i > 0 ? " · " : null}
          <a
            href={`https://www.tower-hub.com${path}`}
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            {`tower-hub.com${path}`}
          </a>
        </span>
      ))}
    </p>
  );
}

export default function GuidePage() {
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        The community strategy the bot&apos;s default buy order is drawn from - so
        you can judge whether you agree with it.
      </p>

      <Card className="gap-3 p-3 text-sm">
        <h2 className={SECTION_HEADING}>Where this account is</h2>
        <p>
          Every guide below describes upgrades this account cannot actually see
          yet, and a guide that does not say so is misleading. Put this first.
        </p>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Measured on the live device</TableHead>
              <TableHead>2026-09-03</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>Coins</TableCell>
              <TableCell>1.77K</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Gems</TableCell>
              <TableCell>40</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Tier</TableCell>
              <TableCell>1</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Highest wave</TableCell>
              <TableCell>10</TableCell>
            </TableRow>
          </TableBody>
        </Table>

        <p className="text-muted-foreground">
          The Workshop&apos;s three tabs hold almost nothing yet:
        </p>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tab</TableHead>
              <TableHead>What is actually there</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>Attack</TableCell>
              <TableCell className="whitespace-normal">
                Damage (lvl value 3), Attack Speed (1.00), Critical Chance
                (1.00%), Critical Factor (x1.20), and a locked Unlock Range
                Upgrades — 50 coins
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Defense</TableCell>
              <TableCell className="whitespace-normal">
                Health (5), Health Regen (0.00/sec), and a locked Unlock
                Defense Upgrades — 75 coins
              </TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Utility</TableCell>
              <TableCell className="whitespace-normal">
                Nothing at all except a locked Unlock Cash Bonuses — 40 coins
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>

        <p>
          So Cash/Wave, Coins/Wave, Cash Bonus, Coins/Kill, Def Abs, Def% and
          Thorns — the seven upgrades every guide below names first — do not
          exist on this account. All seven are behind those three unlock
          tiles, which cost 165 coins together against a balance of 1.77K.
        </p>
        <p className="text-muted-foreground">
          That is why the bot&apos;s shipped buy order leads with the three
          unlocks. It is not a clever heuristic; it is the only move that
          makes the rest of the advice below applicable.
        </p>
      </Card>

      <Card className="gap-3 p-3 text-sm">
        <h2 className={SECTION_HEADING}>Workshop order</h2>
        <p className="text-muted-foreground">
          The consensus splits the Workshop into three jobs, and the order
          between them matters more than the order within them.
        </p>

        <div>
          <h3 className="font-medium">Economy first</h3>
          <ul className="list-inside list-disc space-y-1 text-muted-foreground">
            <li>
              Cash/Wave and Coins/Wave early. Enemies drop very little cash in
              the opening waves, so these are what let anything else get
              bought at all.
            </li>
            <li>
              Switch to Cash Bonus and Coins per Kill around wave 100. Coins
              per kill dominates the economy long term because far more
              multipliers stack onto it; coins per wave has fewer ways to grow
              and flattens out.
            </li>
            <li>
              The community&apos;s framing for this is worth internalising:
              adding a new multiplier usually gains more than pushing an
              already well-developed one further.
            </li>
          </ul>
        </div>

        <div>
          <h3 className="font-medium">Defence second</h3>
          <ul className="list-inside list-disc space-y-1 text-muted-foreground">
            <li>
              Def Abs to roughly 50 workshop levels. This is the single
              most-repeated number in the beginner guides.
            </li>
            <li>Then Def%, which is only worth it once Def Abs is doing real work.</li>
            <li>Health up a level or two early — a little, not a lot.</li>
            <li>
              Thorns held 1% above a fraction breakpoint — 6%, 11%, 21%, 26%.
              Sitting just under a breakpoint wastes the whole investment.
            </li>
            <li>
              Tier 1 is a turtle build: make defence strong enough that
              enemies simply cannot hurt the tower, rather than trying to
              out-damage them.
            </li>
          </ul>
        </div>

        <div>
          <h3 className="font-medium">Attack last</h3>
          <ul className="list-inside list-disc space-y-1 text-muted-foreground">
            <li>Damage and Attack Speed, in that order.</li>
            <li>
              Crit is deliberately deferred. It costs more and scales more
              slowly than everything above it early on. This is why the bot
              ships with Critical Chance and Critical Factor present but
              switched off — the rows exist so you can turn them on when the
              time comes.
            </li>
            <li>
              Later, once Step 2 of the guides opens up: Lifesteal, Knockback
              and Orbs for survival; Damage/meter, Multishot, Rapid Fire,
              Bounce Shot and Super Crit for damage.
            </li>
          </ul>
        </div>

        <SourceLine paths={["/wiki/guide/beginner-guide", "/wiki/guide/coin-guide-basics"]} />
      </Card>

      <Card className="gap-3 p-3 text-sm">
        <h2 className={SECTION_HEADING}>Gems</h2>
        <p className="text-muted-foreground">
          Gems are the currency to be most careful with, and the community
          order is specific:
        </p>
        <ol className="list-inside list-decimal space-y-1 text-muted-foreground">
          <li>Lab slots. First gem purchases, full stop.</li>
          <li>
            Three cards — Attack Speed, Enemy Balance, Coins — plus the card
            slots to equip all three.
          </li>
          <li>The third lab slot.</li>
          <li>Health and Cash cards, plus their slots.</li>
          <li>The fourth lab slot.</li>
          <li>Two of Crit Coin, Wave Skip, Extra Orbs, plus slots.</li>
          <li>The fifth lab slot.</li>
          <li>One Epic module of each type.</li>
          <li>
            Cards, ongoing — enough every two weeks to finish the buy-80-cards
            event (1,600 gems).
          </li>
          <li>More card slots, but only when a card would meaningfully improve the build.</li>
          <li>Relics, selectively — pick the ones you actually want.</li>
          <li>Lab rushing, late game only.</li>
        </ol>

        <p className="text-muted-foreground">What the community warns against:</p>
        <ul className="list-inside list-disc space-y-1 text-muted-foreground">
          <li>Do not chase specific Epics early.</li>
          <li>Do not buy every relic — generally worse value than modules and card levels.</li>
          <li>Do not rush labs early, when so much else competes for the same gems.</li>
          <li>Do not hoard gems waiting for an unannounced banner.</li>
        </ul>

        <SourceLine paths={["/wiki/guide/gem-guide"]} />
      </Card>

      <Card className="gap-3 p-3 text-sm">
        <h2 className={SECTION_HEADING}>Card mechanics</h2>

        <Table>
          <TableBody>
            <TableRow>
              <TableCell>One card</TableCell>
              <TableCell>20 gems</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Ten cards</TableCell>
              <TableCell>200 gems</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Draw odds</TableCell>
              <TableCell>80% Common, 17% Rare, 3% Epic</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>After every Common is maxed</TableCell>
              <TableCell>Rare odds rise to 97%</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Maxing one card</TableCell>
              <TableCell>7 stars, 80 copies, 1,600 gems</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>Card slots</TableCell>
              <TableCell className="whitespace-normal">
                50 gems for the second, rising to 1,000+; 22 slots costs 58,400
                total
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>

        <p className="text-muted-foreground">
          Buying a card is a draw, not a purchase — you do not choose what you
          get. Duplicates are how cards level, so buying is never wasted, but
          it is also never targeted.
        </p>
        <p className="text-muted-foreground">
          There are 12 Commons, 8 Rares and 11 Epics. A few are
          milestone-locked (Recovery Package Chance, Land Mine Stun, Nuke,
          Ultimate Crit, Area of Effect) and cannot drop until you have
          progressed far enough.
        </p>

        <SourceLine paths={["/wiki/card/cards"]} />
      </Card>

      <Card
        role="region"
        aria-label="What the bot will not do"
        className="gap-3 p-3 text-sm"
      >
        <h2 className={SECTION_HEADING}>What the bot will not do</h2>
        <p className="text-muted-foreground">
          This section matters as much as the advice above. Each limit is a
          real constraint, not a missing feature.
        </p>
        <ul className="list-inside list-disc space-y-2 text-muted-foreground">
          <li>
            It will not buy lab slots — and the community order puts those
            above cards. The bot cannot see the Labs screen at all, so a bot
            spending gems on cards while blind to the better purchase would be
            worse than one spending none. This is why card buying ships
            switched off.
          </li>
          <li>
            It will not pick or upgrade Ultimate Weapons. The choice is
            irreversible, each new pick costs more than the last, and it is
            the single most-cited account-damaging mistake in the
            community&apos;s own footguns guide.
          </li>
          <li>
            It will not equip cards. Which cards belong in which slot depends
            on your build and tier — a judgement call the bot has no way to
            make. It buys; you equip.
          </li>
          <li>It will not touch modules, relics, or the shop.</li>
          <li>
            It will not buy anything it cannot read. Every purchase is gated
            on reading your actual balance and the actual price off the
            screen. If either read fails, the visit stops and says so. There
            is no guessing, and there is no brightness fallback — measured on
            your own Cards page, the game marks an unaffordable button by
            desaturating it rather than dimming it, so the brightness check
            cannot tell affordable from unaffordable at all. Reading the
            numbers is the only real gate, so it is the only one trusted.
          </li>
        </ul>

        <SourceLine paths={["/wiki/guide/footguns"]} />
      </Card>
    </div>
  );
}
