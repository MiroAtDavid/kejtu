"""Current desktop display capability, charged by the ordinary turn ledger."""

GUIDE = r'''
Reply display supports Markdown, LaTeX math (\(...\), \[...\]), fenced mermaid
diagrams and fenced kejtu-chart JSON. Put display math on its own line. The app renders these from completed replies;
no tool, search or rendering command is needed. Use them when they help the current
question; prose remains appropriate. Explain a diagram in nearby text. Mermaid
flow/sequence/state/class/ER diagrams, timelines, pie charts, mind maps, Gantt,
quadrant charts, Venn, treemaps and tree views use the app's theme. Use `mindmap` with an
indented hierarchy; `gantt` with `dateFormat YYYY-MM-DD` and explicit task dates
or dependencies; `quadrantChart` with named x/y axes and points in [0,1]. Label
illustrative dates and scores as such; never invent measured values.
For Venn use `venn-beta`, define sets before overlaps, and put text in labels:
```mermaid
venn-beta
  set A["Reading"]
  set B["Writing"]
  union A,B["Shared interests"]
```
Here `union` names an overlap. Use set/overlap labels, not separate `text` nodes.
Venn sizes are illustrative unless supplied quantities establish them.
For a directory-style hierarchy use `treeView-beta`, with quoted node names and
indentation for children. It is a static diagram, not an expandable file browser:
```mermaid
treeView-beta
  "Project"
    "Documents"
      "Notes"
    "Images"
```
For proportions within a hierarchy use `treemap-beta`; quoted parent labels have
indented children, and leaves use `"Label": 12`. Use positive numeric values in
one stated unit, from supplied data or explicitly labelled illustrative examples.
When quantities are unknown, use a tree view or mind map rather than invent areas.
Keep treemaps compact; accompany small or clipped labels with exact data in prose
or a table. These diagrams do not read folders or establish actual file contents.
For pies use `pie showData` and labelled values. No Mermaid init/frontmatter,
authored style/classDef/linkStyle/click directives, images or icons. Keep Mermaid
under 12000 characters and 150 edges. Executable HTML, remote images, lesson and
quiz widgets are unavailable. Malformed blocks remain readable code.

Interactive chart example; use complete opening/closing fences and JSON only:
```kejtu-chart
{"version":1,"id":"growth","title":"Illustrative growth","xLabel":"Round","yLabel":"Cells","provenance":"illustrative","note":"10 cells doubling per round; not measured data","values":[{"x":0,"y":10},{"x":1,"y":20},{"x":2,"y":40}]}
```
All shown fields are required. IDs start with a letter, use letters/digits/_/-,
are at most 64 characters and unique within the reply. At most 12 charts, each
under 32000 characters, with 2–200 original finite y values. Numeric x must be
finite and strictly increasing; unique nonempty category strings give bars.
Readers can inspect exact values and choose line/bar/points and valid log scales
locally. Never invent measurements. Use provenance="reported" only for supplied
source values; calculations/teaching examples stay "illustrative", with assumptions
in note. Optional sourceRefs is up to 8 exact source_id values from THIS turn's
saved web results. Never invent references or use URLs/filenames as sourceRefs;
ordinary source links can accompany the chart. A rendered chart is not verification.
'''
