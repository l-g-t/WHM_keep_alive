import os
import time
import requests
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# -------------------------------
log_buffer = []

def log(msg):
    print(msg)
    log_buffer.append(msg)
# -------------------------------

# Telegram 推送函数
def send_tg_log():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ Telegram 未配置，跳过推送")
        return

    utc_now = datetime.utcnow()
    beijing_now = utc_now + timedelta(hours=8)
    now_str = beijing_now.strftime("%Y-%m-%d %H:%M:%S") + " UTC+8"

    final_msg = f"📌 webhostmost 保活执行日志\n🕒 {now_str}\n\n" + "\n".join(log_buffer)

    for i in range(0, len(final_msg), 3900):
        chunk = final_msg[i:i+3900]
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/sendMessage",
                params={"chat_id": chat_id, "text": chunk},
                timeout=10
            )
            if resp.status_code == 200:
                print(f"✅ Telegram 推送成功 [{i//3900 + 1}]")
            else:
                print(f"⚠️ Telegram 推送失败 [{i//3900 + 1}]: HTTP {resp.status_code}, 响应: {resp.text}")
        except Exception as e:
            print(f"⚠️ Telegram 推送异常 [{i//3900 + 1}]: {e}")

# 从环境变量解析多个账号
accounts_env = os.environ.get("SITE_ACCOUNTS", "")
accounts = []

for item in accounts_env.split(";"):
    if item.strip():
        try:
            username, password = item.split(",", 1)
            accounts.append({"username": username.strip(), "password": password.strip()})
        except ValueError:
            log(f"⚠️ 忽略格式错误的账号项: {item}")

fail_msgs = [
    "Invalid credentials.",
    "Not connected to server.",
    "Error with the login: login size should be between 2 and 50 (currently: 1)"
]

def login_account(playwright, USER, PWD, max_retries: int = 2):
    """
    稳健版登录函数：
    - 支持 username/email 字段的多 selector 回退
    - 提交时尝试多种点击/提交策略
    - 出错时自动重试（默认重试 2 次）
    - 出错时保存截图与部分 HTML 以便调试
    """
    attempt = 0
    while attempt <= max_retries:
        attempt += 1
        log(f"🚀 开始登录账号: {USER} (尝试 {attempt}/{max_retries + 1})")
        browser = None
        context = None
        page = None
        try:
            # 启动浏览器
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()

            # 打开登录页面
            page.goto("https://client.webhostmost.com/login", timeout=60000)
            page.wait_for_load_state("networkidle", timeout=60000)
            time.sleep(1)

            # === Step 1: 寻找用户名/邮箱输入框（容错） ===
            input_selectors = [
                "#inputEmail", "#inputUsername", "#username", "input[name='username']",
                "input[name='email']", "input[type='email']"
            ]
            input_filled = False
            for selector in input_selectors:
                try:
                    page.wait_for_selector(selector, timeout=5000)
                    page.fill(selector, USER)
                    log(f"📝 使用字段 {selector} 填入用户名/邮箱")
                    input_filled = True
                    break
                except Exception:
                    continue

            if not input_filled:
                log("❌ 未找到可用的用户名/邮箱输入框，终止本次尝试")
                raise RuntimeError("no-username-field")

            # === Step 2: 填写密码（容错） ===
            password_selectors = ["#inputPassword", "input[name='password']", "input[type='password']", "#password"]
            pw_filled = False
            for selector in password_selectors:
                try:
                    page.wait_for_selector(selector, timeout=5000)
                    page.fill(selector, PWD)
                    log(f"🔒 使用字段 {selector} 填入密码")
                    pw_filled = True
                    break
                except Exception:
                    continue

            if not pw_filled:
                log("❌ 未找到密码输入框，终止本次尝试")
                raise RuntimeError("no-password-field")

            time.sleep(0.8)

            # === Step 3: 提交表单（多策略） ===
            submitted = False

            # 1) 尝试 role/button 文本点击（优先）
            button_labels = ["Login", "Sign in", "Sign In", "SignIn", "Validate", "Submit", "Log in"]
            for label in button_labels:
                try:
                    page.get_by_role("button", name=label).click(timeout=3000)
                    log(f"🔘 点击角色按钮: '{label}'")
                    submitted = True
                    break
                except Exception:
                    continue

            # 2) 尝试常见 submit 选择器（button/input）
            if not submitted:
                css_candidates = [
                    "button[type='submit']",
                    "input[type='submit']",
                    "button.btn-primary",
                    "button.btn",
                    ".btn-login",
                    ".login-btn",
                    "form button",
                    "form input[type='submit']"
                ]
                for sel in css_candidates:
                    try:
                        # 使用 locator.first 以应对多个匹配
                        locator = page.locator(sel)
                        if locator.count() and locator.first.is_visible():
                            locator.first.click(timeout=4000)
                            log(f"🔘 点击 CSS 按钮: {sel}")
                            submitted = True
                            break
                    except Exception:
                        continue

            # 3) 尝试触发表单 submit via JS
            if not submitted:
                try:
                    # 先尝试找到 form 并调用 submit
                    page.evaluate("""
                        () => {
                            const f = document.querySelector('form');
                            if (f) { f.submit(); return true; }
                            return false;
                        }
                    """)
                    log("🔘 使用 document.querySelector('form').submit() 提交表单（JS 提交）")
                    submitted = True
                except Exception:
                    pass

            # 4) 最后尝试回车键（回退）
            if not submitted:
                try:
                    # 回车可能不会触发，但值得尝试
                    page.press("input:focus, textarea:focus, #inputPassword", "Enter")
                    log("🔘 发送回车键尝试提交")
                    submitted = True
                except Exception:
                    # 如果上面都失败，记录警告，但继续等待（页面可能已自动提交）
                    log("⚠️ 未找到明显的提交方式，已尝试所有策略（Click/CSS/JS/Enter）")

            # === Step 4: 等待跳转或页面变化（加长等待） ===
            try:
                page.wait_for_load_state("networkidle", timeout=60000)
            except Exception:
                # 仍然继续，因为有些页面不进行 full navigation，而是局部渲染
                log("⚠️ page.wait_for_load_state('networkidle') 超时，但将继续检查页面内容")

            # 给异步 JS 留点时间渲染
            time.sleep(3)

            # === Step 5: 智能结果判断（等待短时间以确认结果） ===
            success_signs = [
                "exclusive owner of the following domains",
                "My Services",
                "Client Area",
                "Dashboard"
            ]
            fail_msgs = [
                "Invalid login details",
                "Incorrect username or password",
                "Login failed",
                "Your credentials are incorrect"
            ]

            # 等待并轮询检查一定时间内是否出现成功或失败提示
            check_timeout = 30  # seconds
            poll_interval = 2
            end_time = time.time() + check_timeout
            success_detected = False
            failed_msg = None

            while time.time() < end_time:
                # 检查成功标识
                for sign in success_signs:
                    try:
                        if page.query_selector(f"text={sign}"):
                            success_detected = True
                            break
                    except:
                        continue
                if success_detected:
                    break

                # 检查失败标识
                for msg in fail_msgs:
                    try:
                        if page.query_selector(f"text={msg}"):
                            failed_msg = msg
                            break
                    except:
                        continue
                if failed_msg:
                    break

                # 检查 URL 是否跳转到可能的 dashboard 路径
                try:
                    cur = page.url or ""
                    if any(x in cur for x in ["/dashboard", "/clientarea", "/home", "/account"]):
                        success_detected = True
                        break
                except:
                    pass

                time.sleep(poll_interval)

            # 输出最终结果
            if success_detected:
                log(f"✅ 账号 {USER} 登录成功（检测到成功标识或 URL 跳转）")
                # 成功直接返回，不做重试
                context.close()
                browser.close()
                return
            if failed_msg:
                log(f"❌ 账号 {USER} 登录失败: {failed_msg}")
                # 视场景决定是否重试；这里继续到重试逻辑
                raise RuntimeError("login-failed-detected")

            # 如果既无成功也无明确失败，视为不确定（可能超时或未触发）
            log("⚠️ 未能在等待期内确认登录成功或失败，进入重试/诊断流程")
            raise RuntimeError("login-unknown-state")

        except Exception as e:
            # 失败时保存调试信息（截图 + HTML 前 2000 字）
            try:
                timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
                screenshot_path = f"screenshot_{USER.replace('@','_')}_{timestamp}.png"
                html_path = f"page_{USER.replace('@','_')}_{timestamp}.html"
                if page:
                    try:
                        page.screenshot(path=screenshot_path, full_page=True)
                        log(f"📷 已保存截图: {screenshot_path}")
                    except Exception as ex_s:
                        log(f"⚠️ 保存截图失败: {ex_s}")
                    try:
                        content = page.content()
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(content[:2000])  # 写前 2000 字节，避免过长
                        log(f"📝 已保存页面 HTML 摘要: {html_path}")
                    except Exception as ex_h:
                        log(f"⚠️ 保存 HTML 失败: {ex_h}")
            except Exception:
                pass

            log(f"❌ 账号 {USER} 尝试 ({attempt}) 发生异常: {e}")

            # 如果还有重试机会，则等待小段时间再重试
            if attempt <= max_retries:
                wait_sec = 5 + attempt * 5
                log(f"⏳ 等待 {wait_sec}s 后重试...")
                try:
                    if page:
                        time.sleep(wait_sec)
                except:
                    time.sleep(wait_sec)
                # 关闭资源并进入下一次尝试（finally-ish）
                try:
                    if context:
                        context.close()
                    if browser:
                        browser.close()
                except:
                    pass
                continue
            else:
                # 无重试机会，记录最终失败并返回
                log(f"❌ 账号 {USER} 登录最终失败（{max_retries + 1} 次尝试均未成功）")
                try:
                    if context:
                        context.close()
                    if browser:
                        browser.close()
                except:
                    pass
                return

        finally:
            # 确保资源释放（若未在上面关闭）
            try:
                if context:
                    context.close()
                if browser:
                    browser.close()
            except:
                pass


def run():
    with sync_playwright() as playwright:
        for acc in accounts:
            login_account(playwright, acc["username"], acc["password"])
            time.sleep(2)

if __name__ == "__main__":
    run()
    send_tg_log()  # 发送日志
