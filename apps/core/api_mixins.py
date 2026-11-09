class UserOwnedQuerysetMixin:
    """Every API viewset for a user-owned model mixes this in so a query can
    never cross into another user's rows, regardless of what the client
    requests — isolation is enforced server-side, not by trusting filters."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
