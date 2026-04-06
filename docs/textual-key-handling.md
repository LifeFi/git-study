# Textual 키 이벤트 처리 레퍼런스

> 버전 기준: textual >= 0.85.2

---

## Tab 키 처리 우선순위

Textual에서 Tab 키는 일반 바인딩보다 먼저 포커스 시스템이 처리하므로 특별한 주의가 필요하다.

### 이벤트 처리 순서 (일반 키)

```
focused widget → 부모 위젯 → Screen → App
```

### Tab 키가 특수한 이유

- Textual 내부 포커스 시스템이 Tab을 `focus_next` 동작으로 먼저 처리
- `App.BINDINGS`에 `Binding("tab", "global_tab", priority=True)` 가 있으면 App이 모든 Tab을 가로챔
- `ModalScreen`의 바인딩은 App 바인딩보다 우선하지만, 포커스 시스템 자체는 더 먼저 실행됨

---

## ModalScreen에서 Tab 가로채기

### 동작하지 않는 방법들

| 방법 | 왜 실패하는가 |
|------|--------------|
| `Binding("tab", ..., priority=True)` on Screen | 포커스 시스템이 바인딩 체크보다 먼저 Tab을 소비 |
| `on_key(event)` + `event.prevent_default()` | `on_key`는 포커스 처리 이후에 호출됨 |
| `key_tab()` 메서드 | 동일한 이유 |
| `action_focus_next()` 오버라이드 on Screen | App 레벨의 `focus_next`가 먼저 실행됨 |
| App `action_global_tab()`에서 `isinstance(self.screen, ...)` 체크 | App에 `Binding("tab", "global_tab", priority=True)` 가 있으면 해당 action이 호출되지만, ModalScreen이 활성일 때 action이 실제로 실행되지 않는 경우 존재 |

### 동작하는 방법: `_on_key` 내부 메서드 오버라이드

```python
class CommitPickerScreen(ModalScreen):
    async def _on_key(self, event) -> None:
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            self.action_clear_selection()
        else:
            await super()._on_key(event)
```

`_on_key`는 Textual의 내부 키 처리 진입점으로, 포커스 시스템보다 먼저 실행된다.

---

## App 레벨 전역 Tab 바인딩이 있는 경우

이 프로젝트(`GitStudyAppV2`)는 `Binding("tab", "global_tab", priority=True)`를 App 레벨에 가지고 있다.
ModalScreen에서 Tab을 다르게 처리하려면 **Screen의 `_on_key` 오버라이드**가 가장 확실하다.

```python
# app.py에 isinstance 체크를 추가하는 방식도 가능하지만
# _on_key 오버라이드가 더 명확하고 모달 책임을 Screen 안에 캡슐화
def action_global_tab(self) -> None:
    if isinstance(self.screen, CommitPickerScreen):
        self.screen.action_clear_selection()
        return
    # ... 나머지 처리
```

---

## 참고

- `App.screen`: 스택 최상단(활성) 스크린 반환. ModalScreen이 열려 있으면 해당 모달 반환.
- `App.screen_stack`: 전체 스크린 스택 리스트.
- Textual 공식 문서: https://textual.textualize.io/guide/screens/
