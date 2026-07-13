(function () {
  const dataElement = document.getElementById("document-comment-mention-users");
  if (!dataElement) {
    return;
  }

  let users = [];
  try {
    users = JSON.parse(dataElement.textContent || "[]");
  } catch (_error) {
    users = [];
  }
  if (!users.length) {
    return;
  }

  const mentionInputs = document.querySelectorAll("[data-mention-input]");

  function mentionState(input) {
    const cursor = input.selectionStart || 0;
    const beforeCursor = input.value.slice(0, cursor);
    const match = beforeCursor.match(/(^|\s)@([\w.-]*)$/);
    if (!match) {
      return null;
    }
    return {
      start: beforeCursor.length - match[2].length - 1,
      end: cursor,
      query: match[2].toLowerCase(),
    };
  }

  function matchesForQuery(query) {
    return users
      .filter((user) => {
        const username = (user.username || "").toLowerCase();
        const displayName = (user.display_name || "").toLowerCase();
        return username.includes(query) || displayName.includes(query);
      })
      .slice(0, 6);
  }

  function hideSuggestions(suggestions) {
    suggestions.hidden = true;
    suggestions.innerHTML = "";
    suggestions.dataset.activeIndex = "0";
  }

  function activeButton(suggestions) {
    return suggestions.querySelector(".is-active");
  }

  function setActiveButton(suggestions, index) {
    const buttons = Array.from(suggestions.querySelectorAll("button"));
    if (!buttons.length) {
      return;
    }
    const nextIndex = (index + buttons.length) % buttons.length;
    buttons.forEach((button, buttonIndex) => {
      button.classList.toggle("is-active", buttonIndex === nextIndex);
    });
    suggestions.dataset.activeIndex = String(nextIndex);
  }

  function insertMention(input, state, username) {
    const prefix = input.value.slice(0, state.start);
    const suffix = input.value.slice(state.end);
    const mention = `@${username} `;
    input.value = `${prefix}${mention}${suffix}`;
    const cursor = prefix.length + mention.length;
    input.focus();
    input.setSelectionRange(cursor, cursor);
  }

  function renderSuggestions(input, suggestions, state, matches) {
    suggestions.innerHTML = "";
    matches.forEach((user, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "document-mention-suggestion";
      button.dataset.username = user.username;
      const label = document.createElement("span");
      label.textContent = user.display_name || user.username;
      const username = document.createElement("small");
      username.textContent = `@${user.username}`;
      button.append(label, username);
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        insertMention(input, state, user.username);
        hideSuggestions(suggestions);
      });
      suggestions.appendChild(button);
      if (index === 0) {
        button.classList.add("is-active");
      }
    });
    suggestions.hidden = false;
    suggestions.dataset.activeIndex = "0";
  }

  function updateSuggestions(input, suggestions) {
    const state = mentionState(input);
    if (!state) {
      hideSuggestions(suggestions);
      return null;
    }

    const matches = matchesForQuery(state.query);
    if (!matches.length) {
      hideSuggestions(suggestions);
      return null;
    }

    renderSuggestions(input, suggestions, state, matches);
    return state;
  }

  mentionInputs.forEach((input) => {
    const field = input.closest(".document-comment-mention-field");
    const suggestions = field
      ? field.querySelector("[data-mention-suggestions]")
      : null;
    if (!suggestions) {
      return;
    }

    input.addEventListener("input", () => {
      updateSuggestions(input, suggestions);
    });

    input.addEventListener("keyup", (event) => {
      if (["ArrowUp", "ArrowDown", "Enter", "Tab", "Escape"].includes(event.key)) {
        return;
      }
      updateSuggestions(input, suggestions);
    });

    input.addEventListener("keydown", (event) => {
      if (suggestions.hidden) {
        return;
      }
      if (event.key === "Escape") {
        hideSuggestions(suggestions);
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const currentIndex = Number(suggestions.dataset.activeIndex || "0");
        setActiveButton(
          suggestions,
          currentIndex + (event.key === "ArrowDown" ? 1 : -1),
        );
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        const button = activeButton(suggestions);
        const state = mentionState(input);
        if (!button || !state) {
          return;
        }
        event.preventDefault();
        insertMention(input, state, button.dataset.username);
        hideSuggestions(suggestions);
      }
    });

    input.addEventListener("blur", () => {
      window.setTimeout(() => hideSuggestions(suggestions), 120);
    });
  });
})();
