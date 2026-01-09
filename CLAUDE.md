# Project Guidelines for Powder Highway Trip Planner

## Critical Data Rules

### TICKET Column - DO NOT HALLUCINATE
The TICKET column in the Details table contains ski pass/ticket information that must be accurate.

**NEVER guess or assume ticket types.** When adding or modifying entries:
1. Ask the user for the correct ticket/pass information
2. Do not assume a resort is on Epic, IKON, or independent
3. Do not guess day ticket prices

**Current accurate pass affiliations (as of Jan 2026):**
- Epic: Heavenly, Fernie, Kicking Horse, Kimberley
- IKON: Sun Valley, Revelstoke, Sunshine, Mt Bachelor, Schweitzer, Panorama, Lake Louise
- Independent (day ticket): Warner Canyon ($50), Bogus Basin ($79), Lost Trail Powder ($55), Mt Hood ($99), Whitefish ($105)

If uncertain about any resort's pass affiliation or day ticket price, **ASK THE USER** before making changes.

## File Structure

- `index.html` - Current active version displayed on the site
- `index_option1.html` - Option 1: Oregon/Washington route (Feb 1-25)
- `index_option2.html` - Option 2: Idaho/Montana route (Jan 31 - Feb 25)

When switching options, copy the desired option file to `index.html`.
