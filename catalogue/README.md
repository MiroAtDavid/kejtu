# Kejtu's starter Shelf

The app ships an offline catalogue of nine optional upstream packages.
These optional packages start uninstalled. Projects, Learning & study and
Development come with Kejtu. Optional package payloads are downloaded directly
from their original public GitHub repositories only when someone chooses Install.
There is no catalogue model call, account broker or mirrored package service.

## Sources and maintenance

`sources.json` is the maintainer's selection: original repository, exact commit,
package directory, licence evidence, short presentation and setup requirements.
The shipped snapshot lives at `src/kejtu_next/shelf/catalogue.json`. It records every
selected file's size, Git blob identity and SHA256, the upstream licence paths and
the ordinary package reader's description. Original skill files are never rewritten.

Run `python3 tools/build_shelf_catalogue.py` in an environment with the skills
extra installed. It reads the selected public sources, validates them with the
existing bundle reader, and replaces the snapshot only after every entry passes.
Use `--refresh --output <candidate.json>` to examine newer upstream commits before
updating the maintained selection. Review the diff, update the matching source
pins in `sources.json`, and rebuild. A failure leaves the previous snapshot intact.
No refresh updates installed packages or credentials. New app catalogues can offer
an update only for an installation already recorded as that exact Shelf entry.

Admission here means the original files and supported components are verified;
it is not a security audit or a claim that a hosted service is free. Account access,
service subscriptions, client eligibility and provider limits still apply. OAuth
must be checked with each user's own account. Host-specific extras are shown in
preview. Links are never followed or extracted: a link needed by a component is
refused; an unused host shortcut remains inert upstream bytes.

## Reused owners

The existing bundle reader parses local folders and verified online bytes. The
existing disposable import worker owns download lifetime. The existing public
HTTPS adapter owns address validation. The existing Shelf coordinator records
installation/update/removal and component ownership. Skills retain complete source
files and licence evidence; Connections retains sign-in, keys and tool permissions.
The backend import starts with every part off. Shelf’s explicit Install & enable action then enables skills and available tools, with account sign-in when required. There is no new database schema.

Icons are the original ISC-licensed Lucide SVGs, bundled for offline display.
`desktop/static/shelf-icons/SOURCES.json` records their exact upstream revision and
hashes; its adjacent LICENSE preserves attribution. They are decorative category
icons, not claims of publisher endorsement.

## Repeatable package checks

Run `python3 tools/check_shelf_catalogue.py --output .state/shelf-check-YYYYMMDD`
in a repository environment with the runtime and skills dependencies. This downloads
the pinned original packages and checks installation, exact enabled skill/resource
reads, disable/reopen, removal and reinstall through the existing application
owners in disposable synthetic state. Temporary state stays under the output
directory, on disk. Use `--entry todoist` (repeatable) to
check a specific entry. Each package has a bounded run; failures remain failures
in the incremental `results.json` report. Original upstream effects are never
executed by the instruction-file checks.

The report keeps remote account sign-in and live-model quality separate from
package lifecycle results. A remote package installing successfully does not prove
its account works or that its tools can act. Out-of-package Markdown references
are listed for review; importing a skill does not supply those missing files.
Use an isolated test account for live service checks and keep its credentials in
the existing desktop keyring. No mailbox credentials, browser sessions or login
codes belong in catalogue sources or test reports.

## Local publisher-binary mail recipe

Private maintainer checkouts that include `catalogue/recipes/mail-mcp` can import
the IMAP, drafts and SMTP recipe from that folder. It uses the original
mail-mcp v0.6.1 Linux x86-64 binary. Incoming and outgoing account details are
collected together in Shelf; mailbox organisation, drafts and sending are enabled.
Import the recipe through **Shelf → Install from**. An app with local package account support already has the
machinery this recipe needs. The executable is downloaded from the publisher and verified; it is
not bundled or mirrored. This trial is separate from the online starter listing.
Its README records setup, attribution and supported scope.

A recipe can declare `inputs` (name, label and password masking), binding each
whole environment value as `${NAME}`. Only those explicit inputs are collected;
there is no ambient environment lookup or secret interpolation into arguments.
Account values are entered after installation and stored by Connections in the
keyring. Native install requests leave these bindings empty. `network: true` is
shown in preview; tools stay disabled until setup. `protocol_mode: legacy` selects
the SDK compatibility handshake before launch; omitted means its usual auto mode.
