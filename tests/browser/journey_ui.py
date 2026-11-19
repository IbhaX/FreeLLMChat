from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'app' / 'static'
EDGE = Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')


def verify_dialogs(page: Page) -> None:
    page.locator('#trigger').focus()
    page.evaluate('''() => {
        window.answer = 'pending';
        NovaUI.Dialog.confirm('<img src=x onerror=alert(1)>', {title: 'Remove item'})
            .then(result => {window.answer = result;});
    }''')
    expect(page.get_by_role('dialog', name='Remove item')).to_be_visible()
    assert page.locator('dialog img').count() == 0
    page.keyboard.press('Shift+Tab')
    assert page.evaluate("document.activeElement.closest('dialog') !== null")
    page.keyboard.press('Escape')
    page.wait_for_function('window.answer === false')
    expect(page.locator('#trigger')).to_be_focused()
    expect(page.locator('dialog')).to_have_count(0)

    page.evaluate('''() => {
        window.answer = 'pending';
        NovaUI.Dialog.form({title: 'Connection', fields: [
            {name: 'address', label: 'Address', type: 'url', required: true},
            {name: 'enabled', label: 'Enabled', type: 'checkbox', value: true},
            {name: 'mode', label: 'Mode', type: 'select', value: 'fast',
             options: [{value: 'fast', label: 'Fast'}, {value: 'deep', label: 'Deep'}]}
        ]}).then(result => {window.answer = result;});
    }''')
    expect(page.get_by_label('Address')).to_be_focused()
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.get_by_role('dialog')).to_be_visible()
    assert page.evaluate("window.answer === 'pending'")
    page.get_by_label('Address').fill('https://example.org')
    page.get_by_label('Enabled').uncheck()
    page.get_by_label('Mode', exact=True).select_option('deep')
    page.get_by_role('button', name='Save', exact=True).click()
    page.wait_for_function("window.answer.address === 'https://example.org'")
    assert page.evaluate('window.answer') == {
        'address': 'https://example.org', 'enabled': False, 'mode': 'deep',
    }
    expect(page.locator('#trigger')).to_be_focused()

    page.evaluate('''() => {
        window.answer = 'pending';
        NovaUI.Dialog.prompt('Scene name', {value: 'Existing'})
            .then(result => {window.answer = result;});
    }''')
    expect(page.get_by_label('Scene name')).to_have_value('Existing')
    page.get_by_role('button', name='Cancel', exact=True).click()
    page.wait_for_function('window.answer === null')
    invalid = page.evaluate('''async () => {
        try {
            await NovaUI.Dialog.form({title: 'Invalid', fields: [
                {name: 'name', label: 'One'}, {name: 'name', label: 'Two'}
            ]});
            return false;
        } catch (error) {return error instanceof TypeError;}
    }''')
    assert invalid
    expect(page.locator('dialog')).to_have_count(0)


def verify_menu(page: Page) -> None:
    page.evaluate('''() => {
        window.menu = NovaUI.Menu.bind(document.querySelector('#trigger'),
            document.querySelector('#menu'));
    }''')
    page.locator('#trigger').focus()
    page.keyboard.press('ArrowDown')
    expect(page.locator('#alpha')).to_be_focused()
    page.keyboard.press('ArrowDown')
    expect(page.locator('#beta')).to_be_focused()
    page.keyboard.press('End')
    expect(page.locator('#gamma')).to_be_focused()
    page.keyboard.press('Home')
    expect(page.locator('#alpha')).to_be_focused()
    page.keyboard.press('g')
    expect(page.locator('#gamma')).to_be_focused()
    page.keyboard.press('Escape')
    expect(page.locator('#menu')).to_be_hidden()
    expect(page.locator('#trigger')).to_be_focused()
    page.keyboard.press('ArrowUp')
    expect(page.locator('#gamma')).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.locator('#menu')).to_be_hidden()
    expect(page.locator('#outside')).to_be_focused()
    page.locator('#trigger').click()
    page.locator('#outside').click()
    expect(page.locator('#menu')).to_be_hidden()
    page.locator('#trigger').click()
    page.locator('#beta').click()
    expect(page.locator('#trigger')).to_be_focused()
    page.evaluate('window.menu.destroy()')
    assert page.locator('#trigger').get_attribute('aria-controls') is None
    assert page.locator('#alpha').get_attribute('tabindex') is None
    page.locator('#trigger').click()
    expect(page.locator('#menu')).to_be_hidden()


def verify_notifications(page: Page) -> None:
    page.evaluate('''() => {
        for (let index = 0; index < 5; index++) {
            NovaUI.Toast.show('<img src=x> ' + index, {duration: 0});
        }
    }''')
    expect(page.locator('.nova-toast')).to_have_count(3)
    expect(page.locator('.nova-toasts img')).to_have_count(0)
    assert page.locator('.nova-toast').first.inner_text().startswith('<img src=x> 2')
    for _ in range(3):
        page.get_by_role('button', name='Dismiss notification').first.click()
    expect(page.locator('.nova-toasts')).to_have_count(0)
    page.evaluate("NovaUI.Toast.show('Expired', {tone: 'error', duration: 50})")
    expect(page.locator('.nova-toast')).to_have_count(0)
    page.evaluate('''() => {
        document.querySelector('#states').append(NovaUI.States.error({
            title: 'Could not load scenes', detail: '<img src=x>',
            action: {label: 'Retry', run: () => {window.retried = true;}}
        }));
        document.querySelector('#states').append(NovaUI.States.loading({title: 'Loading scenes'}));
        document.querySelector('#states').append(NovaUI.Icons.create('__proto__'));
    }''')
    expect(page.locator('#states [role="alert"]')).to_contain_text('Could not load scenes')
    expect(page.locator('#states img')).to_have_count(0)
    page.get_by_role('button', name='Retry', exact=True).click()
    assert page.evaluate('window.retried === true')
    expect(page.locator('#states [aria-busy="true"]')).to_have_count(1)


def verify_themes(page: Page) -> None:
    page.evaluate("UITheme.bind(document.querySelector('#theme'))")
    page.get_by_role('button', name='Choose theme').click()
    expect(page.get_by_label('Theme', exact=True).locator('option')).to_have_count(8)
    page.get_by_label('Theme', exact=True).select_option('stage')
    page.get_by_role('button', name='Apply theme', exact=True).click()
    expect(page.locator('html')).to_have_attribute('data-theme', 'stage')
    page.get_by_role('button', name='Choose theme').click()
    page.get_by_label('Theme', exact=True).select_option('light')
    page.keyboard.press('Escape')
    expect(page.locator('html')).to_have_attribute('data-theme', 'stage')
    expect(page.locator('#theme')).to_be_focused()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='Choose theme').click()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.keyboard.press('Escape')


def verify_theme_bootstrap(page: Page) -> None:
    page.evaluate('''() => {
        document.documentElement.dataset.theme = 'apple';
        Object.defineProperty(window, 'localStorage', {
            configurable: true, get() {throw new Error('Storage is blocked');}
        });
    }''')
    bootstrap = (STATIC / 'partials' / 'theme.html').read_text(encoding='utf-8')
    bootstrap = bootstrap.replace('{{default_theme}}', '"stage"')
    page.add_script_tag(content=bootstrap.strip().removeprefix('<script>').removesuffix('</script>'))
    expect(page.locator('html')).to_have_attribute('data-theme', 'stage')
    expect(page.locator('html')).to_have_attribute('data-default-theme', 'stage')


def run(executable: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=executable or playwright.chromium.executable_path, headless=True,
        )
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors: list[str] = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content('''<!doctype html><html data-theme="dark"><head></head><body>
                <button id="trigger">Actions</button><button id="outside">Outside</button>
                <button id="theme">Theme</button><div id="menu" class="ui-menu" hidden>
                <button id="alpha">Alpha</button><button disabled>Disabled</button>
                <button id="beta">Beta</button><button id="gamma">Gamma</button></div>
                <div id="states"></div></body></html>''')
            for stylesheet in ['themes.css', 'ui.css']:
                page.add_style_tag(path=str(STATIC / 'css' / stylesheet))
            for script in ['ui/icons.js', 'ui/dialog.js', 'ui/menu.js',
                           'ui/toast.js', 'ui/states.js', 'theme.js']:
                page.add_script_tag(path=str(STATIC / 'js' / script))
            verify_dialogs(page)
            verify_menu(page)
            verify_notifications(page)
            verify_themes(page)
            verify_theme_bootstrap(page)
            assert not errors, errors
            print('Shared UI journey passed: dialogs, focus, validation, menus, cleanup, '
                  'notifications, states, themes and 390px overflow.')
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', default=str(EDGE) if EDGE.is_file() else '')
    args = parser.parse_args()
    run(args.browser)
