import { PageHeader } from "@/components/PageHeader";
import { SectionNav } from "@/components/SectionNav";
import { SectionCard } from "@/components/ui/section-card";
import {
  Table, TableBody, TableCell, TableRow,
} from "@/components/ui/table";

// Every number and claim here comes from the community wiki the game's
// Discord points newcomers at (tower-hub.com). Where the community disagrees
// with itself, that disagreement is kept rather than papered over.
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

const SECTIONS = [
  { id: "account", label: "This account" },
  { id: "workshop", label: "Workshop order" },
  { id: "gems", label: "Gems" },
  { id: "cards", label: "Card mechanics" },
  { id: "limits", label: "Limits" },
];

export default function GuidePage() {
  return (
    <div className="flex gap-6">
      <SectionNav items={SECTIONS} label="Contents" />

      <div className="flex min-w-0 max-w-3xl flex-1 flex-col gap-4">
      <PageHeader title="Guide" meta="community strategy" />
      <p className="max-w-[68ch] text-sm text-muted-foreground">
        The community strategy the bot&apos;s default buy order is drawn from - so
        you can judge whether you agree with it.
      </p>

      <SectionCard id="account" title="This account" contentClassName="text-sm">
        <p>Check the <a href="/account/" className="text-primary underline">account inspector</a> for saved observations, source evidence, and missing inputs before applying this guide.</p>
        <p className="text-muted-foreground">This is general community advice. Your balances, levels and unlocks must come from account observations; unknown values are not zero or locked.</p>
      </SectionCard>

      <SectionCard id="workshop" title="Workshop order" contentClassName="text-sm">
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
              Switch to Cash Bonus and Coins/Kill around wave 100. Coins
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
      </SectionCard>

      <SectionCard id="gems" title="Gems" contentClassName="text-sm">
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
      </SectionCard>

      <SectionCard id="cards" title="Card mechanics" contentClassName="text-sm">

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
      </SectionCard>

      <SectionCard
        id="limits"
        title="What the bot will not do"
        role="region"
        aria-label="What the bot will not do"
        contentClassName="text-sm"
      >
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
      </SectionCard>
      </div>
    </div>
  );
}
