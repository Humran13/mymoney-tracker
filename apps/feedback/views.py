import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from .forms import FeedbackSubmissionForm
from .models import FeedbackSubmission


@login_required
def feedback_list(request):
    submissions = FeedbackSubmission.objects.filter(user=request.user)
    category = request.GET.get("category", "")
    if category:
        submissions = submissions.filter(category=category)
    paginator = Paginator(submissions, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "feedback/list.html",
        {"page": page, "category": category, "categories": FeedbackSubmission.Category.choices},
    )


@login_required
def feedback_create(request):
    if request.method == "POST":
        form = FeedbackSubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            submission = form.save(commit=False)
            submission.user = request.user
            submission.save()
            messages.success(request, "Thanks — your submission has been received.")
            return redirect("feedback:detail", pk=submission.pk)
    else:
        form = FeedbackSubmissionForm()
    return render(request, "feedback/form.html", {"form": form})


@login_required
def feedback_detail(request, pk):
    submission = get_object_or_404(FeedbackSubmission, pk=pk, user=request.user)
    return render(request, "feedback/detail.html", {"submission": submission})


@login_required
def feedback_attachment(request, pk):
    """Serves a submission's screenshot only to its owner or staff -- never
    a raw, guessable media URL. See apps/feedback/models.py for why this
    isn't just an ImageField under MEDIA_ROOT."""
    submission = get_object_or_404(FeedbackSubmission, pk=pk)
    if submission.user_id != request.user.id and not request.user.is_staff:
        raise Http404
    if not submission.screenshot:
        raise Http404
    content_type, _ = mimetypes.guess_type(submission.screenshot.name)
    return FileResponse(
        submission.screenshot.open("rb"),
        content_type=content_type or "application/octet-stream",
    )
