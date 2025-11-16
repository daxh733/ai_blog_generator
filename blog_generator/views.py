from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.conf import settings
import json
import os
from dotenv import load_dotenv
load_dotenv()
import assemblyai as aai
import traceback
import logging
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_huggingface import HuggingFaceEndpoint

# Initialize Hugging Face model

# import openai
from .models import BlogPost

# Create your views here.
@login_required
def index(request):
    return render(request, 'index.html')

@csrf_exempt
def generate_blog(request):
    if request.method == 'POST':
        # Require authentication for generating blogs. If the frontend posts
        # without a logged-in user, assigning request.user to the BlogPost
        # foreign key will raise an error and produce a 500. Return a clear
        # JSON 401 instead.
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        # Wrap the main work in a broad try/except so any unexpected server
        # error returns JSON (avoids HTML traceback pages which cause the
        # frontend JSON.parse 'Unexpected token <' error).
        try:
            data = json.loads(request.body)
            yt_link = data['link']
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid data sent'}, status=400)

        try:
            # get yt title
            title = yt_title(yt_link)
            if not title:
                return JsonResponse({'error': 'Failed to fetch YouTube title (invalid link or network error)'}, status=400)

            # get transcript
            transcription = get_transcription(yt_link)
            if not transcription:
                return JsonResponse({'error': 'Failed to get transcript'}, status=500)

            # generate the blog content
            blog_content = generate_blog_from_transcription(transcription)
            if not blog_content:
                return JsonResponse({'error': 'Failed to generate blog article'}, status=500)

            # save blog article to database
            new_blog_article = BlogPost.objects.create(
                user=request.user,
                youtube_title=title,
                youtube_link=yt_link,
                generated_content=blog_content,
            )
            new_blog_article.save()

            # return blog article as a response
            return JsonResponse({'content': blog_content})
        except Exception as e:
            # Return JSON error. Include traceback when DEBUG to help debugging.
            if settings.DEBUG:
                return JsonResponse({'error': str(e), 'trace': traceback.format_exc()}, status=500)
            return JsonResponse({'error': 'Internal server error'}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)

def yt_title(link):
    try:
        import yt_dlp
    except Exception:
        return None

    try:
        ydl_opts = {'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=False)
            return info.get('title')
    except Exception:
        return None

def download_audio(link):
    try:
        import yt_dlp
    except Exception:
        return None

    try:
        # Use yt-dlp to download the best audio and convert to mp3 via ffmpeg
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(settings.MEDIA_ROOT, '%(id)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(link, download=True)
            video_id = info.get('id')
            mp3_path = os.path.join(settings.MEDIA_ROOT, f"{video_id}.mp3")
            if os.path.exists(mp3_path):
                return mp3_path
            # fallback: search for a matching mp3
            for fname in os.listdir(settings.MEDIA_ROOT):
                if fname.startswith(video_id) and fname.endswith('.mp3'):
                    return os.path.join(settings.MEDIA_ROOT, fname)
            return None
    except Exception:
        return None

def get_transcription(link):
    audio_file = download_audio(link)
    if not audio_file:
        logging.error("download_audio returned no file for link: %s", link)
        return None

    audio_file = os.path.abspath(audio_file)
    if not os.path.exists(audio_file):
        logging.error("Downloaded audio file not found: %s", audio_file)
        return None

    try:
        size = os.path.getsize(audio_file)
    except Exception:
        size = None
    logging.info("Transcribing file: %s (size=%s bytes)", audio_file, size)

    # Load AssemblyAI API key from environment (do NOT hardcode keys).
    assembly_key = os.getenv('ASSEMBLY_KEY') or os.getenv('ASSEMBLYAI_API_KEY')
    if not assembly_key:
        logging.error('No AssemblyAI API key found in environment (ASSEMBLY_KEY or ASSEMBLYAI_API_KEY)')
        return None
    aai.settings.api_key = assembly_key

    # If a Hugging Face hub token is present in env, ensure the client libraries can see it
    hf_token = os.getenv('HUGGINGFACEHUB_API_TOKEN') or os.getenv('HUGGINGFACE_TOKEN')
    if hf_token:
        os.environ['HUGGINGFACEHUB_API_TOKEN'] = hf_token

    try:
        transcriber = aai.Transcriber()
        transcript = transcriber.transcribe(audio_file)
    except Exception as e:
        logging.exception("AssemblyAI transcription failed for %s", audio_file)
        return None

    # Extract text robustly
    try:
        text = transcript.text
    except Exception:
        try:
            text = transcript.get('text')
        except Exception:
            text = str(transcript)

    logging.info("Transcription finished: %d characters", len(text) if text else 0)
    return text

def generate_blog_from_transcription(transcription):
    # First attempt: use the configured HuggingFaceEndpoint chain (langchain).
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
        # Fallback: use the transformers pipeline locally for summarization.
        # This avoids the "model doesn't support task 'text-generation'" issue
        # when remote endpoint expects a different pipeline type.
        try:
            from transformers import pipeline

            # transformers summarization models often have input length limits.
            # We'll chunk the transcript to a reasonable size and summarize each
            # chunk, then join the summaries.
            summarizer = pipeline("summarization", model="facebook/bart-large-cnn")

            max_chunk = 1000
            chunks = [transcription[i:i+max_chunk] for i in range(0, len(transcription), max_chunk)]
            summaries = []
            for chunk in chunks:
                out = summarizer(chunk, max_length=200, min_length=50, do_sample=False)
                if isinstance(out, list) and len(out) > 0 and 'summary_text' in out[0]:
                    summaries.append(out[0]['summary_text'])
                elif isinstance(out, list) and len(out) > 0 and 'generated_text' in out[0]:
                    summaries.append(out[0]['generated_text'])
            if summaries:
                return "\n\n".join(summaries)
            return None
        except Exception:
            return None




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
        if user is not None:
            login(request, user)
            return redirect('/')
        else:
            error_message = "Invalid username or password"
            return render(request, 'login.html', {'error_message': error_message})
        
    return render(request, 'login.html')

def user_signup(request):
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        repeatPassword = request.POST['repeatPassword']

        if password == repeatPassword:
            try:
                user = User.objects.create_user(username, email, password)
                user.save()
                login(request, user)
                return redirect('/')
            except:
                error_message = 'Error creating account'
                return render(request, 'signup.html', {'error_message':error_message})
        else:
            error_message = 'Password do not match'
            return render(request, 'signup.html', {'error_message':error_message})
        
    return render(request, 'signup.html')

def user_logout(request):
    logout(request)
    return redirect('/')