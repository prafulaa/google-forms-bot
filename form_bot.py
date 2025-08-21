import random
import time
from dataclasses import dataclass
from typing import List, Optional

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


@dataclass
class BotConfig:
	form_url: str
	min_delay_s: float = 0.2
	max_delay_s: float = 0.8
	headless: bool = True
	page_load_timeout_s: int = 20
	wait_timeout_s: int = 15
	speed_mode: str = "normal"  # normal | fast | ultra


class GoogleFormBot:
	def __init__(self, config: BotConfig, logger: Optional[callable] = None):
		# Apply speed profiles
		if config.speed_mode == "fast":
			config.min_delay_s = 0.08
			config.max_delay_s = 0.22
			config.wait_timeout_s = min(config.wait_timeout_s, 8)
		elif config.speed_mode == "ultra":
			config.min_delay_s = 0.02
			config.max_delay_s = 0.06
			config.wait_timeout_s = min(config.wait_timeout_s, 5)

		self.config = config
		self._driver: Optional[webdriver.Chrome] = None
		self._stop_requested = False
		self._log = logger or (lambda msg: None)

	# ------------- lifecycle -------------
	def start(self) -> None:
		options = webdriver.ChromeOptions()
		# Faster page readiness; proceed when DOMContentLoaded fires
		options.page_load_strategy = "eager"
		if self.config.headless:
			options.add_argument("--headless=new")
		options.add_argument("--disable-gpu")
		options.add_argument("--window-size=1280,1000")
		options.add_argument("--disable-extensions")
		options.add_argument("--disable-notifications")
		options.add_argument("--disable-background-networking")
		options.add_argument("--disable-renderer-backgrounding")
		options.add_argument("--disable-background-timer-throttling")
		options.add_argument("--mute-audio")
		# Disable images to speed up loads
		options.add_experimental_option(
			"prefs",
			{
				"profile.managed_default_content_settings.images": 2,
				"profile.default_content_setting_values.notifications": 2,
			},
		)
		# Selenium Manager (no webdriver_manager dependency)
		self._driver = webdriver.Chrome(options=options)
		self._driver.set_page_load_timeout(self.config.page_load_timeout_s)

	def stop(self) -> None:
		self._stop_requested = True

	def quit(self) -> None:
		if self._driver:
			try:
				self._driver.quit()
			except Exception:
				pass
			self._driver = None

	# ------------- utils -------------
	def _sleep_human(self, lo: Optional[float] = None, hi: Optional[float] = None) -> None:
		lo = self.config.min_delay_s if lo is None else lo
		hi = self.config.max_delay_s if hi is None else hi
		time.sleep(random.uniform(lo, hi))

	def _wait(self, timeout: Optional[int] = None):
		return WebDriverWait(self._driver, timeout or self.config.wait_timeout_s)

	def _click_js(self, elem) -> None:
		self._driver.execute_script("arguments[0].click();", elem)

	# ------------- navigation -------------
	def open_form(self) -> None:
		assert self._driver is not None
		self._log("Opening form...")
		self._driver.get(self.config.form_url)
		# wait for form main container (shorter in fast modes)
		try:
			self._wait().until(
				EC.presence_of_element_located((By.CSS_SELECTOR, "form[action]"))
			)
		except TimeoutException:
			pass
		self._sleep_human(0.15, 0.35 if self.config.speed_mode == "ultra" else 0.5)

	def click_submit(self) -> None:
		# Try typical submit button
		buttons = self._driver.find_elements(By.XPATH, "//span[normalize-space()='Submit']/ancestor::div[@role='button']")
		if not buttons:
			buttons = self._driver.find_elements(By.XPATH, "//div[@role='button' and .//span[contains(., 'Submit')]]")
		if not buttons:
			buttons = self._driver.find_elements(By.CSS_SELECTOR, "div[role='button']")
			if buttons:
				self._click_js(buttons[-1])
				return
			raise RuntimeError("Submit button not found")
		self._click_js(buttons[0])

	def click_submit_another(self) -> bool:
		try:
			link = WebDriverWait(self._driver, 3 if self.config.speed_mode in ("fast", "ultra") else 6).until(
				EC.presence_of_element_located((By.XPATH, "//a[contains(., 'Submit another response')]"))
			)
			self._click_js(link)
			return True
		except TimeoutException:
			return False

	# ------------- answering logic -------------
	def _answer_all_questions(self) -> None:
		# Find all visible question blocks
		blocks = self._driver.find_elements(By.CSS_SELECTOR, "div[role='listitem']")
		for block in blocks:
			if self._stop_requested:
				return

			# For grid questions: multiple radiogroups per block (one per row)
			radio_groups = block.find_elements(By.CSS_SELECTOR, "div[role='radiogroup']")
			if radio_groups:
				for rg in radio_groups:
					radios = rg.find_elements(By.CSS_SELECTOR, "div[role='radio']")
					selectable = [r for r in radios if r.get_attribute("aria-disabled") != "true"]
					if not selectable:
						continue
					choice = self._choose_radio_like_human(selectable)
					self._click_js(choice)
					self._sleep_human()
				continue

			# Single radio question (no explicit radiogroup wrapper)
			radios = block.find_elements(By.CSS_SELECTOR, "div[role='radio']")
			if radios:
				selectable = [r for r in radios if r.get_attribute("aria-disabled") != "true"]
				if selectable:
					choice = self._choose_radio_like_human(selectable)
					self._click_js(choice)
					self._sleep_human()
				continue

			# Checkboxes (rare in this form; pick 1-2 randomly)
			checkboxes = block.find_elements(By.CSS_SELECTOR, "div[role='checkbox']")
			if checkboxes:
				selectable = [c for c in checkboxes if c.get_attribute("aria-disabled") != "true"]
				if selectable:
					k = 1 if len(selectable) == 1 else (1 if self.config.speed_mode == "ultra" else random.choice([1, 1, 2]))
					for cb in random.sample(selectable, k=k):
						self._click_js(cb)
						self._sleep_human()
				continue

			# Dropdowns
			dropdowns = block.find_elements(By.CSS_SELECTOR, "div[role='listbox']")
			if dropdowns:
				try:
					self._click_js(dropdowns[0])
					self._sleep_human(0.05, 0.15)
					options = self._driver.find_elements(By.CSS_SELECTOR, "div[role='option']")
					visible = [o for o in options if o.is_displayed()]
					if visible:
						self._click_js(random.choice(visible))
						self._sleep_human()
				except Exception:
					pass
				continue

			# Short answer / textareas (not expected in provided form)
			inputs = block.find_elements(By.CSS_SELECTOR, "input[type='text'], input[type='email'], textarea")
			if inputs:
				try:
					val = self._sample_short_answer(block.text)
					inputs[0].clear()
					inputs[0].send_keys(val)
					self._sleep_human()
				except Exception:
					pass

	def _choose_radio_like_human(self, radios: List) -> object:
		# Bias towards middle answers slightly for Likert scales
		n = len(radios)
		if n <= 2:
			return random.choice(radios)
		weights = []
		mid = (n - 1) / 2.0
		for i in range(n):
			# triangular distribution centered at mid
			w = (n - abs(i - mid))
			weights.append(w)
		total = sum(weights)
		r = random.uniform(0, total)
		upto = 0
		for i, w in enumerate(weights):
			if upto + w >= r:
				return radios[i]
			upto += w
		return radios[-1]

	def _sample_short_answer(self, context_text: str) -> str:
		# Minimal placeholder; adjust if your form has short-answer fields
		if "name" in context_text.lower():
			first_names = ["Rita", "Sanjay", "Aarav", "Mina", "Kiran", "Sita", "Prakash", "Anita"]
			last_names = ["Sharma", "Karki", "Thapa", "Adhikari", "Gurung", "Rai", "KC", "Bista"]
			return f"{random.choice(first_names)} {random.choice(last_names)}"
		return "N/A"

	# ------------- public API -------------
	def submit_n_responses(self, n: int) -> int:
		assert self._driver is not None
		submitted = 0
		for i in range(n):
			if self._stop_requested:
				break
			self._log(f"Starting response {i + 1} of {n}")
			if i == 0:
				self.open_form()
			else:
				# Try 'Submit another response'; else reload
				if not self.click_submit_another():
					self._driver.get(self.config.form_url)
					self._sleep_human(0.15, 0.35 if self.config.speed_mode == "ultra" else 0.5)

			# Answer all questions and submit
			self._answer_all_questions()
			self._sleep_human(0.05, 0.15)
			self.click_submit()
			self._sleep_human(0.2, 0.5 if self.config.speed_mode == "ultra" else 0.7)

			submitted += 1
			self._log(f"Submitted {submitted}/{n}")

			# Short human pause between submissions
			self._sleep_human(0.2, 0.6 if self.config.speed_mode == "ultra" else 0.9)

		return submitted
