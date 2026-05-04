"""Curated quote rotation for the plain (non-LLM) brief renderer.

Goals:
- No clichés, no AI-aphorism slop. Real attributions only.
- Mix of fields (literature, philosophy, science, programming, activism).
- Per-day deterministic pick so the same date always shows the same quote
  (hash-by-date), spreading the rotation naturally across the year.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Quote:
    text: str
    author: str


QUOTES: tuple[Quote, ...] = (
    Quote("You have power over your mind — not outside events. Realize this, and you will find strength.", "Marcus Aurelius"),
    Quote("Almost everything will work again if you unplug it for a few minutes, including you.", "Anne Lamott"),
    Quote("All that you touch you Change. All that you Change Changes you.", "Octavia Butler"),
    Quote("If you surrender to the wind, you can ride it.", "Toni Morrison"),
    Quote("Talk is cheap. Show me the code.", "Linus Torvalds"),
    Quote("Not everything that is faced can be changed, but nothing can be changed until it is faced.", "James Baldwin"),
    Quote("It is good to have an end to journey toward; but it is the journey that matters, in the end.", "Ursula K. Le Guin"),
    Quote("One's life has value so long as one attributes value to the life of others.", "Simone de Beauvoir"),
    Quote("You can tell whether a man is clever by his answers. You can tell whether a man is wise by his questions.", "Naguib Mahfouz"),
    Quote("What you seek is seeking you.", "Rumi"),
    Quote("Out of suffering have emerged the strongest souls; the most massive characters are seared with scars.", "Kahlil Gibran"),
    Quote("Between stimulus and response there is a space. In that space is our power to choose our response.", "Viktor Frankl"),
    Quote("In the depth of winter, I finally learned that within me there lay an invincible summer.", "Albert Camus"),
    Quote("The whole problem with the world is that fools and fanatics are always so certain of themselves, and wiser people so full of doubts.", "Bertrand Russell"),
    Quote("It is never too late to be what you might have been.", "George Eliot"),
    Quote("The cure for boredom is curiosity. There is no cure for curiosity.", "Dorothy Parker"),
    Quote("When I dare to be powerful, to use my strength in the service of my vision, then it becomes less and less important whether I am afraid.", "Audre Lorde"),
    Quote("Somewhere, something incredible is waiting to be known.", "Carl Sagan"),
    Quote("I would rather have questions that can't be answered than answers that can't be questioned.", "Richard Feynman"),
    Quote("Premature optimization is the root of all evil.", "Donald Knuth"),
    Quote("Simplicity is prerequisite for reliability.", "Edsger Dijkstra"),
    Quote("The most dangerous phrase in the language is 'we've always done it this way.'", "Grace Hopper"),
    Quote("The best way to predict the future is to invent it.", "Alan Kay"),
    Quote("A word after a word after a word is power.", "Margaret Atwood"),
    Quote("Education is not the filling of a pail, but the lighting of a fire.", "W.B. Yeats"),
    Quote("When I let go of what I am, I become what I might be.", "Lao Tzu"),
    Quote("When you come to a fork in the road, take it.", "Yogi Berra"),
    Quote("I am my own muse. I am the subject I know best.", "Frida Kahlo"),
    Quote("Life can only be understood backwards; but it must be lived forwards.", "Søren Kierkegaard"),
    Quote("A book must be the axe for the frozen sea within us.", "Franz Kafka"),
    Quote("Another world is not only possible, she is on her way. On a quiet day, I can hear her breathing.", "Arundhati Roy"),
    Quote("A classic is a book that has never finished saying what it has to say.", "Italo Calvino"),
    Quote("What lies behind us and what lies before us are tiny matters compared to what lies within us.", "Ralph Waldo Emerson"),
    Quote("Development requires the removal of major sources of unfreedom: poverty as well as tyranny.", "Amartya Sen"),
    Quote("Tell me, what is it you plan to do with your one wild and precious life?", "Mary Oliver"),
    Quote("You can't cross the sea merely by standing and staring at the water.", "Rabindranath Tagore"),
    Quote("The most radical revolutionary will become a conservative the day after the revolution.", "Hannah Arendt"),
    Quote("While we do our good works let us not forget that the real solution lies in a world in which charity will have become unnecessary.", "Chinua Achebe"),
    Quote("I've learned that people will forget what you said, but people will never forget how you made them feel.", "Maya Angelou"),
    Quote("First, solve the problem. Then, write the code.", "John Johnson"),
)


def pick_quote_for(today: date) -> Quote:
    """Deterministic per-date pick. Same calendar date always returns the same quote."""
    iso = today.isoformat()
    # Stable, language-agnostic hash: sum of byte values is enough for rotation.
    bucket = sum(iso.encode("utf-8")) % len(QUOTES)
    return QUOTES[bucket]
