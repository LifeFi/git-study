# Textual 색상 시스템 레퍼런스

> 소스: `textual/design.py`, `textual/theme.py`  
> 버전 기준: 현재 프로젝트 `.venv` 설치본

---

## 1. 베이스 색상 (Theme 파라미터)

`Theme(...)` 생성자에 직접 지정하는 11개 기본 색상:

| 파라미터 | 설명 | 기본값 |
|---|---|---|
| `primary` | 메인 테마 색 (필수) | — |
| `secondary` | 보조 테마 색 | primary와 동일 |
| `accent` | 강조 색 | primary와 동일 |
| `warning` | 경고 색 | primary와 동일 |
| `error` | 에러 색 | secondary와 동일 |
| `success` | 성공 색 | secondary와 동일 |
| `foreground` | 기본 텍스트 색 | background의 inverse |
| `background` | 최하단 배경 | dark: `#121212`, light: `#efefef` |
| `surface` | 위젯 배경 (background보다 약간 밝음) | dark: `#1e1e1e`, light: `#f5f5f5` |
| `panel` | 패널/사이드바 배경 (surface보다 약간 밝음) | `surface.blend(primary, 0.1)` |
| `boost` | 레이어 강조용 오버레이 | 대비색 4% alpha (흰색 또는 검정) |

### `$boost` 설명

```python
boost = contrast_text.with_alpha(0.04)
```

배경에 대비되는 색(흰/검정)의 **4% 투명도** 오버레이.  
다크 테마에서 `panel`이 지정되지 않으면 `surface + boost`로 자동 계산됨.  
레이어 깊이감을 표현하는 용도.

---

## 2. Shade 자동 생성 변수

아래 12개 베이스 색상 각각에 **darken-1~3 / lighten-1~3** 총 7단계가 자동 생성됨:

- `primary`, `secondary`, `background`, `primary-background`, `secondary-background`
- `surface`, `panel`, `boost`, `warning`, `error`, `success`, `accent`

```css
/* 예시: primary */
$primary-darken-3
$primary-darken-2
$primary-darken-1
$primary
$primary-lighten-1
$primary-lighten-2
$primary-lighten-3
```

---

## 3. 자동 생성 CSS 변수 전체 목록

### 텍스트

```css
$text                        /* auto 87% */
$text-muted                  /* auto 60% */
$text-disabled               /* auto 38% */
$text-primary                /* primary 색조 텍스트 */
$text-secondary
$text-accent
$text-warning
$text-error
$text-success
$foreground-muted            /* foreground 60% alpha */
$foreground-disabled         /* foreground 38% alpha */
```

### Muted 배경 (색상 on muted 패턴용)

```css
$primary-muted               /* primary + background 70% blend */
$secondary-muted
$accent-muted
$warning-muted
$error-muted
$success-muted
```

사용 예: `color: $text-primary; background: $primary-muted;`

### 커서 / 블록 선택

```css
$block-cursor-foreground
$block-cursor-background          /* = $primary */
$block-cursor-text-style          /* = bold */
$block-cursor-blurred-foreground
$block-cursor-blurred-background  /* = primary 30% alpha */
$block-cursor-blurred-text-style  /* = none */
$block-hover-background           /* = boost 10% alpha */
```

### 테두리 / Surface

```css
$border                      /* = $primary */
$border-blurred              /* = surface 약간 darken */
$surface-active              /* 포커스된 surface */
```

### 스크롤바

```css
$scrollbar
$scrollbar-hover
$scrollbar-active            /* = $primary */
$scrollbar-background
$scrollbar-corner-color
$scrollbar-background-hover
$scrollbar-background-active
```

### 링크

```css
$link-background
$link-background-hover       /* = $primary */
$link-color
$link-style                  /* = underline */
$link-color-hover
$link-style-hover            /* = bold not underline */
```

### Footer

```css
$footer-foreground
$footer-background           /* = $panel */
$footer-key-foreground       /* = $accent */
$footer-key-background
$footer-description-foreground
$footer-description-background
$footer-item-background
```

### Input

```css
$input-cursor-background
$input-cursor-foreground
$input-cursor-text-style
$input-selection-background  /* = primary-lighten-1 40% alpha */
```

### Markdown

```css
$markdown-h1-color  $markdown-h1-background  $markdown-h1-text-style
$markdown-h2-color  $markdown-h2-background  $markdown-h2-text-style
$markdown-h3-color  $markdown-h3-background  $markdown-h3-text-style
$markdown-h4-color  $markdown-h4-background  $markdown-h4-text-style
$markdown-h5-color  $markdown-h5-background  $markdown-h5-text-style
$markdown-h6-color  $markdown-h6-background  $markdown-h6-text-style
```

### Button

```css
$button-foreground
$button-color-foreground
$button-focus-text-style     /* = b reverse */
```

---

## 4. 테마 적용 방법

### 내장 테마 목록

```
textual-dark, textual-light, textual-ansi
nord, gruvbox
catppuccin-mocha, catppuccin-latte, catppuccin-frappe, catppuccin-macchiato
dracula, tokyo-night, monokai, flexoki
solarized-light, solarized-dark
rose-pine, rose-pine-moon, rose-pine-dawn
atom-one-dark, atom-one-light
```

### 런타임에서 전환

```python
self.app.theme = "dracula"
```

### 앱 기본 테마 지정

```python
class MyApp(App):
    def on_mount(self) -> None:
        self.theme = "tokyo-night"
```

### 커스텀 테마 등록

```python
from textual.theme import Theme
from textual.app import App

my_theme = Theme(
    name="my-theme",
    primary="#FF6B6B",
    secondary="#4ECDC4",
    background="#2C3E50",
    surface="#34495E",
    panel="#3D566E",
    dark=True,
    variables={
        # CSS 변수 직접 오버라이드
        "block-cursor-text-style": "none",
        "footer-key-foreground": "#FF6B6B",
        "input-selection-background": "#FF6B6B 30%",
        "border": "#4ECDC4",
    },
)

class MyApp(App):
    def on_mount(self) -> None:
        self.register_theme(my_theme)
        self.theme = "my-theme"
```

### CSS에서 변수 사용

```css
MyWidget {
    background: $surface;
    color: $foreground;
    border: tall $primary;
}

MyWidget:focus {
    background: $panel;
    border: tall $border;
}

.warning-label {
    color: $text-warning;
    background: $warning-muted;
}
```

### Markup에서 변수 사용

```python
# Rich markup 안에서도 CSS 변수 참조 가능
self.write("[$accent]강조 텍스트[/]")
self.write("[$warning on $warning-muted]경고![/]")
```
