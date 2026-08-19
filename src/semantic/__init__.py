"""Inferred entities on top of mirrored documents.

Connectors mirror what Slack, Drive, and Notion contain. This package adds what
those documents *mean*: the people, projects, and tasks they describe, what is
known about each, and how they connect.

Everything turns on one split.

    person:jane                 an **entity**: identity only — the name, email,
         ^   ^                  or id that says which person this is. No
   about |   | about            permission parent, and no grants at all.
         |   |
    fact-+   +-fact             a **fact**: everything one document says about
  (private)   (public)          that entity, whose permission parent IS the
                                document it was read out of.

An entity is a name the workspace may know. A fact is a claim only that
document's readers may read. Because a fact's parent is its source, access
follows the path a Slack message's already does — same kernel walk, nothing
materialised, no second rule.

The entity's own visibility is **derived** from those facts rather than stored:
you may know Jane exists exactly when you may read something about Jane. That is
why there is no provenance table and no grant to withdraw — deleting a
document's facts is the whole of the access change, and revoking a channel grant
upstream reaches the entities inferred from it because nothing was ever copied
down.

Read the modules in this order:

  `config`    the declared ontology — what each entity type represents and what
              identifies one — and how a declared type compiles to the same
              `NodeTypeSpec` a Slack message uses
  `registry`  loading that into the process, plus the ontology every stack
              starts with
  `models`    what one extraction pass produces
  `extract`   the tool loop: name entities, record facts, draw links
  `store`     identity resolution, fact persistence, retraction
  `publish`   an admin path for appending an ontology revision
  `__main__`  the worker: a stream consumer that reconciles on change, and a
              watermark sweeper

Facts are owned by their source, which is what makes reconciliation exact rather
than a judgement call: a changed document has its previous claims deleted and
re-derived, a deleted one has them deleted and not replaced, and in both cases
the entities survive.
"""
