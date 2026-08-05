from __future__ import annotations

from django.db.models import Q

from doksio.documents.models import DocumentMetadataField, DocumentSpace


def document_space_path_chain(space: DocumentSpace) -> list[str]:
    parts = [part for part in space.path.strip("/").split("/") if part]
    if not parts:
        return [space.path]
    paths = []
    current = ""
    for part in parts:
        current = f"{current}/{part}"
        paths.append(current)
    return paths


def metadata_field_slug_is_available(
    *,
    space: DocumentSpace,
    slug: str,
    propagate_to_child_spaces: bool = True,
    exclude_field: DocumentMetadataField | None = None,
) -> bool:
    ancestor_paths = document_space_path_chain(space)[:-1]
    fields = DocumentMetadataField.objects.filter(tenant=space.tenant, slug=slug).filter(
        Q(space=space)
        | Q(
            space__path__in=ancestor_paths,
            propagate_to_child_spaces=True,
        )
    )
    if propagate_to_child_spaces:
        fields = fields | DocumentMetadataField.objects.filter(
            tenant=space.tenant,
            slug=slug,
            space__path__startswith=f"{space.path.rstrip('/')}/",
        )
    if exclude_field is not None:
        fields = fields.exclude(id=exclude_field.id)
    return not fields.exists()


def effective_metadata_fields(
    space: DocumentSpace,
    *,
    active_only: bool = True,
) -> list[DocumentMetadataField]:
    fields = (
        DocumentMetadataField.objects.select_related("space", "choice_list")
        .prefetch_related("choice_list__items")
        .filter(
            tenant=space.tenant,
            space__path__in=document_space_path_chain(space),
        )
        .order_by("space__path", "sort_order", "name", "id")
    )
    if active_only:
        fields = fields.filter(is_active=True)

    fields = fields.filter(
        Q(space=space) | Q(propagate_to_child_spaces=True)
    )

    effective_fields = []
    seen_slugs = set()
    for field in fields:
        if field.slug in seen_slugs:
            continue
        effective_fields.append(field)
        seen_slugs.add(field.slug)
    return effective_fields
