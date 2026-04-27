import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
import random

# Настройки для yt-dlp
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
}

ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

# Путь к ffmpeg.exe
current_dir = os.path.dirname(__file__)
project_dir = os.path.dirname(current_dir)
ffmpeg_path = os.path.join(project_dir, "ffmpeg", "bin", "ffmpeg.exe")

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# Глобальные переменные для каждого сервера
queues = {}          # Очереди треков
current_songs = {}   # Текущий играющий трек
loops = {}           # Режим повтора (True/False)
volumes = {}         # Уровень громкости

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.uploader = data.get('uploader')
        self.thumbnail = data.get('thumbnail')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, executable=ffmpeg_path, **ffmpeg_options), data=data)

def format_time(seconds):
    """Преобразует секунды в формат ЧЧ:ММ:СС или ММ:СС"""
    if not seconds:
        return "Неизвестно"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"

# Настройка бота (отключаем встроенную команду help)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

def get_queue(guild_id):
    """Возвращает очередь для указанного сервера"""
    if guild_id not in queues:
        queues[guild_id] = asyncio.Queue()
    return queues[guild_id]

async def play_next(ctx, guild_id):
    """Воспроизводит следующий трек из очереди"""
    queue = get_queue(guild_id)

    # Проверка режима повтора
    if loops.get(guild_id, False) and guild_id in current_songs:
        current = current_songs[guild_id]
        ctx.voice_client.play(current, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, guild_id), bot.loop))
        duration_str = format_time(current.duration)
        await ctx.send(f"🔄 Повтор включён. Сейчас играет: **{current.title}**\n⏱️ Длительность: {duration_str}")
        return

    # Воспроизведение следующего трека
    if not queue.empty():
        next_song = await queue.get()
        current_songs[guild_id] = next_song
        ctx.voice_client.play(next_song, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, guild_id), bot.loop))

        duration_str = format_time(next_song.duration)
        volume_level = volumes.get(guild_id, 50)
        await ctx.send(f"🎵 Сейчас играет: **{next_song.title}**\n"
                       f"👤 Исполнитель: {next_song.uploader}\n"
                       f"⏱️ Длительность: {duration_str}\n"
                       f"🔊 Громкость: {volume_level}%")
    else:
        # Очередь пуста — отключаемся
        await ctx.send("💤 Очередь закончилась. Бот отключается от голосового канала. Для новой музыки используйте команду `!play`")
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
        if guild_id in queues:
            del queues[guild_id]
        if guild_id in current_songs:
            del current_songs[guild_id]

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} успешно запущен!')
    print(f'🎙️ FFmpeg найден: {os.path.exists(ffmpeg_path)}')
    print(f'📊 Загружено команд: {len(bot.commands)}')
    print('-' * 40)

@bot.event
async def on_command_error(ctx, error):
    """Обработчик ошибок команд"""
    if isinstance(error, commands.CommandNotFound):
        await ctx.send(f"❌ Команда не найдена. Напишите `!команды` для просмотра списка доступных команд.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Не хватает аргументов. Используйте `!команды` для получения справки.")
    else:
        await ctx.send(f"❌ Произошла ошибка: {str(error)[:100]}")

# ==================== КОМАНДЫ НА АНГЛИЙСКОМ ====================

@bot.command(name='play', aliases=['p'])
async def play(ctx, *, query):
    """Воспроизвести музыку с YouTube"""
    # Проверка: пользователь в голосовом канале
    if not ctx.author.voice:
        await ctx.send("❌ Вы не находитесь в голосовом канале. Подключитесь к голосовому каналу и попробуйте снова.")
        return

    voice_channel = ctx.author.voice.channel

    # Подключение к голосовому каналу
    if ctx.voice_client is None:
        await voice_channel.connect()
        await ctx.send(f"✅ Бот подключился к каналу **{voice_channel.name}**")
    elif ctx.voice_client.channel != voice_channel:
        await ctx.voice_client.move_to(voice_channel)
        await ctx.send(f"✅ Бот перемещён в канал **{voice_channel.name}**")

    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    # Поиск трека
    async with ctx.typing():
        await ctx.send(f"🔍 Поиск: **{query}**...")
        try:
            player = await YTDLSource.from_url(query, loop=bot.loop, stream=True)
        except Exception as e:
            await ctx.send(f"❌ Не удалось загрузить трек. Проверьте правильность запроса или попробуйте другую ссылку.\nОшибка: {str(e)[:80]}")
            return

    duration_str = format_time(player.duration)

    # Если музыка уже играет — добавляем в очередь
    if ctx.voice_client.is_playing():
        await queue.put(player)
        queue_position = queue.qsize()
        await ctx.send(f"📌 Трек добавлен в очередь!\n"
                       f"🎵 Название: **{player.title}**\n"
                       f"⏱️ Длительность: {duration_str}\n"
                       f"📊 Позиция в очереди: {queue_position}")
    else:
        # Иначе начинаем воспроизведение
        current_songs[guild_id] = player
        volumes[guild_id] = 50
        player.volume = 0.5
        ctx.voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, guild_id), bot.loop))
        await ctx.send(f"🎉 Воспроизведение начато!\n"
                       f"🎵 **{player.title}**\n"
                       f"👤 Исполнитель: {player.uploader}\n"
                       f"⏱️ Длительность: {duration_str}\n"
                       f"🔊 Громкость: 50% (изменить командой `!volume`)")

@bot.command(name='skip', aliases=['s'])
async def skip(ctx):
    """Пропустить текущий трек"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await ctx.send("❌ Сейчас ничего не воспроизводится. Используйте `!play` для запуска музыки.")
        return

    guild_id = ctx.guild.id
    current = current_songs.get(guild_id)
    track_name = current.title if current else "текущий трек"

    ctx.voice_client.stop()
    await ctx.send(f"⏩ Трек **{track_name}** пропущен. Воспроизводится следующий.")

@bot.command(name='stop')
async def stop(ctx):
    """Остановить воспроизведение и очистить очередь"""
    if ctx.voice_client:
        guild_id = ctx.guild.id
        queues[guild_id] = asyncio.Queue()  # Очистка очереди
        if guild_id in current_songs:
            current_songs[guild_id] = None
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        await ctx.send("🛑 Воспроизведение остановлено. Очередь очищена. Бот отключён от голосового канала.")
    else:
        await ctx.send("❌ Бот не находится в голосовом канале.")

@bot.command(name='queue', aliases=['q'])
async def show_queue(ctx):
    """Показать текущую очередь треков"""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    if queue.empty():
        await ctx.send("📭 Очередь пуста. Добавьте треки с помощью команды `!play`")
        return

    # Получаем список треков из очереди
    temp_queue = list(queue._queue)

    # Информация о текущем треке
    current = current_songs.get(guild_id)
    current_text = f"🎶 **Сейчас играет:** {current.title}\n" if current else "🎶 **Сейчас:** ничего не играет\n"

    # Список следующих треков
    songs_list = []
    for i, song in enumerate(temp_queue[:10], start=1):
        duration_str = format_time(song.duration)
        songs_list.append(f"`{i}.` **{song.title}** `[{duration_str}]`")

    message = current_text + "\n**📀 Очередь:**\n" + "\n".join(songs_list)
    if len(temp_queue) > 10:
        message += f"\n... и ещё **{len(temp_queue) - 10}** треков в очереди"

    await ctx.send(message)

@bot.command(name='clear')
async def clear_queue(ctx):
    """Очистить очередь (текущий трек продолжает играть)"""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)
    queue_size = queue.qsize()

    if queue_size == 0:
        await ctx.send("📭 Очередь уже пуста.")
        return

    queues[guild_id] = asyncio.Queue()
    await ctx.send(f"🧹 Очередь очищена. Удалено треков: {queue_size}. Текущий трек продолжает играть.")

@bot.command(name='leave', aliases=['disconnect'])
async def leave(ctx):
    """Отключить бота от голосового канала"""
    if ctx.voice_client:
        guild_id = ctx.guild.id
        if guild_id in queues:
            del queues[guild_id]
        if guild_id in current_songs:
            del current_songs[guild_id]
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Бот отключён от голосового канала. До свидания!")
    else:
        await ctx.send("❌ Бот не находится в голосовом канале.")

@bot.command(name='now', aliases=['np'])
async def now_playing(ctx):
    """Показать информацию о текущем треке"""
    guild_id = ctx.guild.id
    current = current_songs.get(guild_id)

    if not current or not ctx.voice_client or not ctx.voice_client.is_playing():
        await ctx.send("💤 Сейчас ничего не воспроизводится. Используйте `!play` для запуска музыки.")
        return

    duration_str = format_time(current.duration)
    volume_level = volumes.get(guild_id, 50)

    embed = discord.Embed(
        title="🎵 Сейчас играет",
        description=f"**{current.title}**",
        color=discord.Color.blue()
    )
    embed.add_field(name="Исполнитель", value=current.uploader, inline=True)
    embed.add_field(name="Длительность", value=duration_str, inline=True)
    embed.add_field(name="Громкость", value=f"{volume_level}%", inline=True)
    embed.set_footer(text="Приятного прослушивания!")

    if current.thumbnail:
        embed.set_thumbnail(url=current.thumbnail)

    await ctx.send(embed=embed)

@bot.command(name='volume', aliases=['vol'])
async def volume(ctx, vol: int = None):
    """Изменить громкость (0-100)"""
    if not ctx.voice_client or not ctx.voice_client.source:
        await ctx.send("❌ Сначала запустите воспроизведение командой `!play`")
        return

    if vol is None:
        current_vol = volumes.get(ctx.guild.id, 50)
        await ctx.send(f"🔊 Текущая громкость: **{current_vol}%**")
        return

    if vol < 0 or vol > 100:
        await ctx.send("❌ Громкость должна быть в диапазоне от 0 до 100.")
        return

    volumes[ctx.guild.id] = vol
    ctx.voice_client.source.volume = vol / 100

    if vol == 0:
        await ctx.send("🔇 Звук выключен. Для включения используйте `!volume 50`")
    elif vol == 100:
        await ctx.send("🔊 Громкость установлена на максимум (100%)")
    else:
        await ctx.send(f"🎚️ Громкость изменена на **{vol}%**")

@bot.command(name='loop', aliases=['repeat'])
async def loop(ctx):
    """Включить/выключить повтор текущего трека"""
    guild_id = ctx.guild.id

    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await ctx.send("❌ Сначала запустите воспроизведение командой `!play`")
        return

    current_state = loops.get(guild_id, False)
    loops[guild_id] = not current_state

    if loops[guild_id]:
        await ctx.send("🔁 Режим повтора **включён**. Текущий трек будет зациклен.")
    else:
        await ctx.send("🔂 Режим повтора **выключен**. После окончания трека будет воспроизведён следующий из очереди.")

@bot.command(name='shuffle', aliases=['mix'])
async def shuffle(ctx):
    """Перемешать очередь в случайном порядке"""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    if queue.qsize() < 2:
        await ctx.send("❌ Недостаточно треков для перемешивания. Добавьте хотя бы 2 трека в очередь.")
        return

    # Преобразуем очередь в список, перемешиваем и создаём новую очередь
    items = list(queue._queue)
    random.shuffle(items)

    new_queue = asyncio.Queue()
    for item in items:
        await new_queue.put(item)
    queues[guild_id] = new_queue

    await ctx.send(f"🎲 Очередь перемешана. Новый порядок содержит {len(items)} треков.")

@bot.command(name='remove', aliases=['del'])
async def remove(ctx, index: int):
    """Удалить трек из очереди по номеру (1 = первый в очереди)"""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    if queue.qsize() == 0:
        await ctx.send("❌ Очередь пуста. Нечего удалять.")
        return

    if index < 1:
        await ctx.send("❌ Номер должен быть положительным числом (начиная с 1).")
        return

    items = list(queue._queue)

    if index > len(items):
        await ctx.send(f"❌ В очереди только {len(items)} треков. Укажите номер от 1 до {len(items)}.")
        return

    removed_title = items[index - 1].title
    items.pop(index - 1)

    # Создаём новую очередь без удалённого трека
    new_queue = asyncio.Queue()
    for item in items:
        await new_queue.put(item)
    queues[guild_id] = new_queue

    await ctx.send(f"🗑️ Трек **{removed_title}** удалён из очереди.")

@bot.command(name='pause')
async def pause(ctx):
    """Поставить воспроизведение на паузу"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Воспроизведение поставлено на паузу. Для продолжения используйте `!resume`")
    else:
        await ctx.send("❌ Сейчас ничего не воспроизводится.")

@bot.command(name='resume')
async def resume(ctx):
    """Продолжить воспроизведение после паузы"""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Воспроизведение продолжено.")
    elif ctx.voice_client and ctx.voice_client.is_playing():
        await ctx.send("✅ Музыка уже играет.")
    else:
        await ctx.send("❌ Сейчас ничего не воспроизводится. Используйте `!play` для запуска музыки.")

@bot.command(name='next_track', aliases=['next', 'n'])
async def next_track(ctx):
    """Принудительно переключиться на следующий трек"""
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await ctx.send("❌ Сейчас ничего не воспроизводится. Используйте `!play` для запуска музыки.")
        return

    guild_id = ctx.guild.id
    current = current_songs.get(guild_id)
    track_name = current.title if current else "текущий трек"

    ctx.voice_client.stop()  # Остановка текущего трека вызовет play_next
    await ctx.send(f"⏭️ Принудительное переключение. Трек **{track_name}** завершён досрочно.")

# ==================== РУССКИЕ КОМАНДЫ ====================

@bot.command(name='играй', aliases=['плей'])
async def play_ru(ctx, *, query):
    """Воспроизвести музыку (русская версия)"""
    await play(ctx, query=query)

@bot.command(name='пропусти', aliases=['скип'])
async def skip_ru(ctx):
    """Пропустить трек (русская версия)"""
    await skip(ctx)

@bot.command(name='стоп')
async def stop_ru(ctx):
    """Остановить воспроизведение (русская версия)"""
    await stop(ctx)

@bot.command(name='очередь', aliases=['лист', 'список'])
async def queue_ru(ctx):
    """Показать очередь (русская версия)"""
    await show_queue(ctx)

@bot.command(name='очисти')
async def clear_ru(ctx):
    """Очистить очередь (русская версия)"""
    await clear_queue(ctx)

@bot.command(name='уйди', aliases=['отключись', 'пока'])
async def leave_ru(ctx):
    """Отключить бота (русская версия)"""
    await leave(ctx)

@bot.command(name='сейчас', aliases=['текущий', 'инфо'])
async def now_ru(ctx):
    """Показать текущий трек (русская версия)"""
    await now_playing(ctx)

@bot.command(name='громкость', aliases=['звук'])
async def volume_ru(ctx, vol: int = None):
    """Изменить громкость (русская версия)"""
    await volume(ctx, vol=vol)

@bot.command(name='повтор', aliases=['цикл'])
async def loop_ru(ctx):
    """Включить/выключить повтор (русская версия)"""
    await loop(ctx)

@bot.command(name='перемешай', aliases=['тасуй'])
async def shuffle_ru(ctx):
    """Перемешать очередь (русская версия)"""
    await shuffle(ctx)

@bot.command(name='удали', aliases=['убери'])
async def remove_ru(ctx, index: int):
    """Удалить трек из очереди (русская версия)"""
    await remove(ctx, index=index)

@bot.command(name='пауза')
async def pause_ru(ctx):
    """Поставить на паузу (русская версия)"""
    await pause(ctx)

@bot.command(name='продолжи', aliases=['возобнови'])
async def resume_ru(ctx):
    """Продолжить воспроизведение (русская версия)"""
    await resume(ctx)

@bot.command(name='следующий', aliases=['дальше'])
async def next_track_ru(ctx):
    """Принудительно переключиться на следующий трек (русская версия)"""
    await next_track(ctx)

@bot.command(name='команды', aliases=['хелп', 'помощь', 'справка'])
async def commands_list(ctx):
    """Показать список всех команд"""
    embed = discord.Embed(
        title="🎵 Музыкальный бот — список команд",
        description="Все команды начинаются с символа `!`\nДоступны команды на русском и английском языке.",
        color=discord.Color.green()
    )

    embed.add_field(name="🎵 Управление воспроизведением",
                    value="`!play / !играй <название>` - воспроизвести трек\n"
                          "`!skip / !пропусти` - пропустить текущий трек\n"
                          "`!next_track / !следующий` - принудительно переключить на следующий трек\n"
                          "`!pause / !пауза` - поставить на паузу\n"
                          "`!resume / !продолжи` - продолжить\n"
                          "`!stop / !стоп` - остановить и очистить всё\n"
                          "`!now / !сейчас` - информация о текущем треке",
                    inline=False)

    embed.add_field(name="📋 Управление очередью",
                    value="`!queue / !очередь` - показать очередь\n"
                          "`!clear / !очисти` - очистить очередь\n"
                          "`!remove / !удали <номер>` - удалить трек из очереди\n"
                          "`!shuffle / !перемешай` - перемешать очередь",
                    inline=False)

    embed.add_field(name="⚙️ Настройки",
                    value="`!volume / !громкость [0-100]` - изменить громкость\n"
                          "`!loop / !повтор` - включить/выключить повтор\n"
                          "`!leave / !уйди` - отключить бота",
                    inline=False)

    embed.add_field(name="ℹ️ Прочее",
                    value="`!команды / !help` - показать это сообщение",
                    inline=False)

    embed.set_footer(text="Пример использования: !play Imagine Dragons Believer")
    await ctx.send(embed=embed)

# ==================== ЗАПУСК БОТА ====================

TOKEN = 'ВАШ_ТОКЕ'  # Вставьте ваш токен здесь

if __name__ == "__main__":
    print("🚀 Запуск музыкального бота...")
    print(f"📁 Путь к FFmpeg: {ffmpeg_path}")
    print(f"✅ FFmpeg доступен: {os.path.exists(ffmpeg_path)}")
    print("-" * 40)
    bot.run(TOKEN)
