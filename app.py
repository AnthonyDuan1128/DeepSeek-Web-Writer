import streamlit as st
import openai
import sqlite3
import threading
import time
from contextlib import closing

# --- 数据库设置 ---
# 使用 contextlib.closing 确保数据库连接和游标在使用后能被安全关闭

def setup_database():
    """初始化数据库，创建书籍表"""
    with closing(sqlite3.connect('writing_progress.db', check_same_thread=False)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    total_chapters INTEGER NOT NULL,
                    current_chapter INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    full_text TEXT DEFAULT ''
                )
            ''')
        conn.commit()

def add_book_to_db(title, total_chapters):
    """向数据库中添加一本新书，并返回其ID"""
    with closing(sqlite3.connect('writing_progress.db', check_same_thread=False)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute(
                "INSERT INTO books (title, total_chapters, status) VALUES (?, ?, ?)",
                (title, total_chapters, '排队中...')
            )
            book_id = cursor.lastrowid
        conn.commit()
    return book_id

def update_book_progress(book_id, current_chapter, status, new_content):
    """更新书籍的进度、状态和内容"""
    with closing(sqlite3.connect('writing_progress.db', check_same_thread=False)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute("SELECT full_text FROM books WHERE id = ?", (book_id,))
            current_text = cursor.fetchone()[0]
            # 将新内容追加到旧内容之后
            full_text = current_text + new_content if current_text else new_content
            cursor.execute(
                "UPDATE books SET current_chapter = ?, status = ?, full_text = ? WHERE id = ?",
                (current_chapter, status, full_text, book_id)
            )
        conn.commit()

def get_book_info(book_id):
    """根据ID获取书籍信息"""
    with closing(sqlite3.connect('writing_progress.db', check_same_thread=False)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute("SELECT * FROM books WHERE id = ?", (book_id,))
            return cursor.fetchone()

def get_all_books():
    """获取所有书籍的列表"""
    with closing(sqlite3.connect('writing_progress.db', check_same_thread=False)) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute("SELECT id, title, status FROM books ORDER BY id DESC")
            return cursor.fetchall()


# --- AI 交互模块 ---

def call_deepseek_api(api_key, model, messages):
    """调用Deepseek API的函数"""
    try:
        # Deepseek API 兼容 OpenAI 的 SDK
        client = openai.OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"调用API时出错: {e}")
        return None


# --- 后台写作线程 ---

def writing_process(api_key, model, book_title, num_chapters, book_id):
    """AI写作的核心流程，在独立的线程中运行"""
    # 获取当前已有的文本内容
    book_info = get_book_info(book_id)
    full_text = book_info[5] # full_text

    for chapter_num in range(1, num_chapters + 1):
        update_book_progress(book_id, chapter_num, f"正在生成第 {chapter_num} 章...", "")
        
        # 1. 生成三个不同的草稿
        drafts = []
        for i in range(3):
            prompt_draft = f"你是一位富有想象力的小说家。请根据以下小说的已有内容，续写第 {chapter_num} 章。\n\n【书名】: {book_title}\n\n【已有内容】:\n{full_text}"
            messages = [{"role": "user", "content": prompt_draft}]
            draft = call_deepseek_api(api_key, model, messages)
            if draft:
                drafts.append(draft)
            time.sleep(1) # 避免过快的API请求

        if len(drafts) < 3:
            update_book_progress(book_id, chapter_num, "错误：生成草稿失败", "")
            return

        # 2. 让AI选择最佳版本
        prompt_select = (
            f"你是一位资深编辑。请从以下为小说《{book_title}》的第 {chapter_num} 章写的三个草稿版本中，选择一个与上下文衔接最自然、情节最吸引人、文笔最好的版本。请不要添加任何评论或解释，直接输出你选择的那个版本的全文。\n\n"
            f"【上下文（之前的内容）】:\n{full_text}\n\n"
            f"--- 草稿版本 1 ---\n{drafts[0]}\n\n"
            f"--- 草稿版本 2 ---\n{drafts[1]}\n\n"
            f"--- 草稿版本 3 ---\n{drafts[2]}\n---"
        )
        messages = [{"role": "user", "content": prompt_select}]
        best_chapter = call_deepseek_api(api_key, model, messages)

        if not best_chapter:
            update_book_progress(book_id, chapter_num, "错误：选择最佳版本失败", "")
            return

        # 3. 更新数据库
        new_content_for_db = f"\n\n---\n\n## 第 {chapter_num} 章\n\n{best_chapter}"
        full_text += new_content_for_db
        status = '写作中...' if chapter_num < num_chapters else '已完成'
        update_book_progress(book_id, chapter_num, status, new_content_for_db)
        time.sleep(1)


# --- Streamlit 用户界面 ---

st.set_page_config(page_title="AI 小说协作作家", layout="wide")
st.title("🤖 AI 小说协作作家")
st.caption("由 Deepseek & Streamlit 驱动")

# 初始化数据库
setup_database()

# 会话状态管理，用于跟踪当前查看的书籍ID
if 'current_book_id' not in st.session_state:
    st.session_state.current_book_id = None

# --- 侧边栏：用户输入和项目列表 ---
with st.sidebar:
    st.header("开启新项目")
    api_key = st.text_input("Deepseek API 密钥", type="password", help="您的API密钥将仅用于本次会话。")
    model_name = st.text_input("模型名称", value="deepseek-chat")
    book_title = st.text_input("书籍标题")
    num_chapters = st.number_input("计划写作章数", min_value=1, max_value=100, value=10)

    if st.button("🚀 开始写作", use_container_width=True):
        if not all([api_key, model_name, book_title]):
            st.warning("请填写所有必填项！")
        else:
            book_id = add_book_to_db(book_title, num_chapters)
            st.session_state.current_book_id = book_id
            
            # 创建并启动后台线程
            thread = threading.Thread(
                target=writing_process,
                args=(api_key, model_name, book_title, num_chapters, book_id)
            )
            thread.daemon = True # 保证主程序退出时线程也退出
            thread.start()
            
            st.success(f"《{book_title}》已加入写作队列！现在您可以关闭网页，写作任务会在后台继续。")

    st.divider()

    st.header("📚 资源库")
    all_books = get_all_books()
    if not all_books:
        st.write("还没有任何项目。")
    else:
        for book in all_books:
            # 点击按钮，切换当前查看的书籍
            if st.button(f"📖 {book[1]} ({book[2]})", key=f"book_{book[0]}", use_container_width=True):
                st.session_state.current_book_id = book[0]

# --- 主界面：显示写作进度和内容 ---
if st.session_state.current_book_id:
    book_info = get_book_info(st.session_state.current_book_id)
    if book_info:
        book_id, title, total_chapters, current_chapter, status, full_text = book_info
        
        st.header(f"当前作品: 《{title}》")
        
        # 进度条和状态
        col1, col2 = st.columns([3, 1])
        with col1:
            progress = min(current_chapter / total_chapters, 1.0)
            st.progress(progress, text=f"进度: {current_chapter}/{total_chapters} 章")
        with col2:
            st.metric(label="状态", value=status)
            
        # 显示已生成的内容
        st.subheader("已生成内容")
        with st.container(height=600):
            st.markdown(full_text)

        # 如果仍在写作中，则设置页面定时刷新以模拟“流式”更新
        if status not in ['已完成', '错误：生成草稿失败', '错误：选择最佳版本失败']:
            st.info("页面正在自动刷新以获取最新进度...")
            time.sleep(10) # 延迟10秒
            st.experimental_rerun()
else:
    st.info("👈 请从左侧侧边栏开启一个新项目，或从资源库中选择一个已有项目进行查看。")
