from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings
import json
import os
import traceback
import logging
import requests

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_huggingface import HuggingFaceEndpoint

from assemblyai import AssemblyAI
from .models import BlogPost


# ===========================
# 🔥 AssemblyAI Setup
# ===========================

ASSEMBLYAI_KEY = os.getenv("ASSEMBLYAI_KEY")
client = AssemblyAI(api_key=ASSEMBLYAI_KEY)


def get_youtube_title(yt_url):
    """Fetch YouTube video title using AssemblyAI."""
    try:
        resp = requests.get(
            "https://api.assemblyai.com/v2/youtube",
            params={"url": yt_url},
            headers={"authorization": ASSEMBLYAI_KEY}
        )
        data = resp.json()
        return data.get("title")
    except:
        return None


def get_transcription_from_youtube(yt_url):
    """Get transcript from YouTube link directly using AssemblyAI (no yt-dlp)."""
    try:
        # Create transcript request
        transcript = client.transcripts.create({
            "audio_url": yt_url
        })

        # Poll until finished
        transcript = client.transcripts.get(transcript.id)
        while transcript.status not in ["completed", "error"]:
            transcript = client.transcripts.get(transcript.id)

        if transcript.status == "completed":
            return transcript.text
        return None

    except Exception as e:
        print("AssemblyAI Error:", e)
        return None


# ===========================
# 🔥 Blog Generation (HF Model)
# ===========================

def generate_blog_from_transcription(transcription):
    try:
        llm = HuggingFaceEndpoint(
            repo_id="facebook/bart-large-cnn",
            task="summarization",
            temperature=0.5,
            max_new_tokens=500,
        )

        prompt = PromptTemplate.from_template(
            """
            Write a detailed, SEO-friendly blog post summarizing the following video transcript:
            {text}
            """
        )

        chain = prompt | llm | StrOutputParser()
        generated_content = chain.invoke({"text": transcription})
        return generated_content

    except Exception:
        try:
            from transformers import pipeline

            summarizer = pipeline("summarization", model="facebook/bart-large-cnn")

            max_chunk = 1000
            chunks = [transcription[i:i+max_chunk] for i in range(0, len(transcription), max_chunk)]
            summaries = []

            for chunk in chunks:
                out = summarizer(chunk, max_length=200, min_length=50, do_sample=False)
                if isinstance(out, list) and 'summary_text' in out[0]:
                    summaries.append(out[0]['summary_text'])

            if summaries:
                return "\n\n".join(summaries)
            return None
        except:
            return None


# ===========================
# 🔥 VIEWS
# ===========================

@login_required
def index(request):
    return render(request, 'index.html')


@csrf_exempt
def generate_blog(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=405)

    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    # Parse incoming JSON
    try:
        data = json.loads(request.body)
        yt_link = data['link']
    except:
        return JsonResponse({'error': 'Invalid data sent'}, status=400)

    try:
        # 1️⃣ Get YouTube Title
        title = get_youtube_title(yt_link)
        if not title:
            return JsonResponse({'error': 'Failed to fetch YouTube title'}, status=400)

        # 2️⃣ Get Transcript (AssemblyAI)
        transcription = get_transcription_from_youtube(yt_link)
        if not transcription:
            return JsonResponse({'error': 'Failed to fetch transcript'}, status=500)

        # 3️⃣ Generate Blog from Transcript
        blog_content = generate_blog_from_transcription(transcription)
        if not blog_content:
            return JsonResponse({'error': 'Failed to generate blog content'}, status=500)

        # 4️⃣ Save to DB
        BlogPost.objects.create(
            user=request.user,
            youtube_title=title,
            youtube_link=yt_link,
            generated_content=blog_content,
        )

        # 5️⃣ Return Output
        return JsonResponse({'content': blog_content})

    except Exception as e:
        if settings.DEBUG:
            return JsonResponse({
                'error': str(e),
                'trace': traceback.format_exc()
            }, status=500)
        return JsonResponse({'error': 'Internal server error'}, status=500)


def blog_list(request):
    blog_articles = BlogPost.objects.filter(user=request.user)
    return render(request, "all-blogs.html", {'blog_articles': blog_articles})


def blog_details(request, pk):
    blog_article_detail = BlogPost.objects.get(id=pk)
    if request.user == blog_article_detail.user:
        return render(request, 'blog-details.html', {'blog_article_detail': blog_article_detail})
    else:
        return redirect('/')


def user_login(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']

        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect('/')
        return render(request, 'login.html', {'error_message': 'Invalid username or password'})

    return render(request, 'login.html')


def user_signup(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        repeatPassword = request.POST['repeatPassword']

        if password != repeatPassword:
            return render(request, 'signup.html', {'error_message': 'Passwords do not match'})

        try:
            user = User.objects.create_user(username, email, password)
            login(request, user)
            return redirect('/')
        except:
            return render(request, 'signup.html', {'error_message': 'Error creating account'})

    return render(request, 'signup.html')


def user_logout(request):
    logout(request)
    return redirect('/')
