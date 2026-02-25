# Malevič.js [![npm version](https://img.shields.io/npm/v/malevic.svg?style=flat)](https://www.npmjs.com/package/malevic)

Minimalistic reactive UI library.
As simple as possible.
Extendable.
*5KB gzipped (7KB with animations)*.

![Malevič.js logo](https://rawgit.com/alexanderby/malevic/master/logo-256x256.svg)

Suitable for building framework-independent dynamic widgets as well as small web apps.
Create, manage state, animate!

## Samples

- [Data visualization](https://alexanderby.github.io/malevic-samples/)
- [Chrome extension UI](https://chrome.google.com/webstore/detail/dark-reader/eimadpbcbfnmbkopoojfekhnkhdbieeh)

## Basic example

- `m()` function creates a DOM node specification that looks like `{type, props, children}`.
- `render()` function renders nodes inside a DOM element.
If differences with existing DOM nodes are found,
necessary nodes or attributes are replaced.

```javascript
import {m} from 'malevic';
import {render} from 'malevic/dom';

render(document.body,
    m('h3', {class: 'heading'},
        'Hello, World!'
    )
);
```

Shorthand tag functions can be used:

```javascript
import {render, tags} from 'malevic/dom';
const {h3, strong} = tags;

render(document.body,
    h3({class: 'heading'},
        'Hello, ',
        strong('World!')
    )
);
```

or

```javascript
import {render, tag} from 'malevic/dom';
const anchor = tag('a');

render(document.body,
    anchor({href: '/'},
        'Click Me',
    )
);
```

When DOM node already exists, a `sync()` function can be used:

```javascript
import {sync, tags} from 'malevic/dom';
const {body, h1} = tags;

sync(document.body,
    body({class: 'app'},
        h1(
            'Hello, World!'
        )
    )
);
```

Functions can be used as components like this:

```javascript
import {sync, tags} from 'malevic/dom';
const {body, span, h1, button} = tags;

function Button(props, ...children) {
    return button({onclick: props.handler},
        span({class: 'icon'}),
        ...children
    );
}

sync(document.body,
    body({class: 'app'},
        h1('App'),
        Button({handler: (e) => alert(e.target)},
            'Click me'
        )
    )
);
```

For more complex components, that need to store some state or access the DOM nodes,
the `component()` function should be used:

```javascript
import {component, tags} from 'malevic/dom';
const {button, img, span} = tags;

const Button = component((context, props, ...children) => {
    const store = context.getStore({clicks: 0});
    const onClick = () => {
        store.clicks = store.clicks + 1;
        context.refresh();
    };
    return button({onclick: onClick},
        img({src: props.icon}),
        span('Clicks' + store.clicks),
        ...children
    );
});
```

See the description for the `context` object below.

## JSX

`m` pragma should be used to make it work with **JSX**:
- Babel:
```json
{
    "plugins": [
        ["transform-react-jsx", {
            "pragma": "m"
        }]
    ]
}
```
- TypeScript:
```json
{
    "compilerOptions": {
        "jsx": "react",
        "jsxFactory": "m"
    }
}
```

A component written in JSX will look like:
```jsx
import {m} from 'malevic';
import {sync} from 'malevic/dom';

function Button({handler}, ...children) {
    return (
        <button onclick={handler}>
            {...children}
        </button>
    );
}

sync(document.body, (
    <body class="app">
        <h1>App</h1>
        <Button handler={(e) => alert(e.target)}>
            Click me
        </Button>
    </body>
));
```

`m` is a factory function for creating a spec tree from JSX, so `import {m} from 'malevic';` should be included in every JSX or TSX file.

## Listening to events

If attribute starts with `on`,
a corresponding event listener is added to a DOM element
(or removed if value is `null`).

```jsx
<button onclick={(e) => alert(e.target)} />
```

## Manipulating class list and styles

- Possible **class** attribute values:

```jsx
<div
    class="view active"
    class={['view', props.isActive ? 'active' : null]}
    class={{'view': true, 'active': props.isActive}}
/>
```
- Possible **style** attribute values:
```jsx
<div
    style="background: red !important; opacity: 0;"
    style={{'background': 'red !important', 'opacity': 0}}
/>
```

## Lifecycle management

- `oncreate` handler will be invoked after DOM node is created and appended to parent.
- `onupdate` handler will be invoked after all attributes of existing DOM node were synchronized.
- `onrender` handler will be invoked after DOM node is created or updated.
- `onremove` handler will be invoked after DOM node was removed.

```jsx
function Heading() {
    return (
        <h4
            oncreate={(domNode) => {
                domNode.classList.add('rendered');
                domNode.textContent = 'Hello';
            }}
        ></h4>
    );
}

render(document.body, <Heading/>);
```

It is possible to assign lifecycle handlers for components as well:

```javascript
import {component, tags} from 'malevic/dom';
const {div} = tags;

const Component = component((context) => {
    context.onCreate((domNode) => domNode.classList.add('init'));
    context.onRemove((domNode) => domNode.parentNode == null);
    context.onUpdate((domNode) => domNode === context.node);
    context.onRender((domNode) => domNode.dataset.renderCount = ++renderCount);

    return div('Hello');
}
```

With JSX it will look like:
```jsx
import {getContext} from 'malevic/dom';

function Component() {
    const context = getContext();

    context.onCreate((domNode) => domNode.classList.add('init'));
    context.onRemove((domNode) => domNode.parentNode == null);
    context.onUpdate((domNode) => domNode === context.node);
    context.onRender((domNode) => domNode.dataset.renderCount = ++renderCount);

    return <div>Hello</div>;
}
```

## Optimizing component re-renders

When virtual DOM checks should be skipped, `context.leave()` function could be used.

```jsx
function Component(props) {
    const {prev} = getContext();

    if (prev && prev.props.value === props.value) {
        return context.leave();
    }

    return <Nested value={props.value} />;
}
```

Here `context.prev` property returns a previous component specification.

## Virtual nodes matching

By default virtual children are matched by a component type or an element tag name.
`key` property should be used to prevent detaching virtual nodes when children order changes:

```jsx
render(target, (
    <List>
        <Item key={0} />
        <Item key={1} />
        <Item key={2} />
    </List>
));
// Attached items: 0, 1, 2

render(target, (
    <List>
        <Item key={3} />
        <Item key={2} />
        <Item key={1} />
    </List>
));
// Attached items: 3
// Detached items: 0
// Updated items: 1, 2
```

Any value can be used for `key`, matching is done by a strict `===` comparison.

## Getting DOM node before rendering	

 It is possible to get a parent DOM node before updating the DOM tree.	

 ```jsx	
import {m} from 'malevic';
import {render, getContext} from 'malevic/dom';

function App() {
    const {parent} = getContext();
    const rect = parent.getBoundingClientRect();
    return (<Array>
        <header></header>
        <main>
            <h3>Size</h3>
            <p>{`Width: ${rect.width}`}</p>
            <p>{`Height: ${rect.height}`}</p>
        </main>
        <footer></footer>
    </Array>);
}

render(document.body, <App/>);	
```

After a component is rendered,
`context.node` property will return an attached DOM node.
If a component creates multiple DOM nodes, `context.nodes` property will return all of them:

```jsx
function Many({items}) {
    const {node, nodes} = getContext();
    node; // header
    nodes; // [header, ..., footer]
    return (
        <Array>
            <header/>
            {...items.map((item) => <span>{item}</span>)}
            <footer/>
        </Array>
    );
}
```

When there is a need of getting a descending DOM node before
rendering it's child, then inline functions could be used:

```jsx
render(document.body, (
    <h1>Body size</h1>
    <div class="wrapper">
        {({parent, node}) => {
            const rect = parent.getBoundingClientRect();
            return <Array>
                <p>Width: {rect.width}px</p>
                <p>Height: {rect.height}px</p>
            </Array>;
        }}
    </div>
));
```

## Using DOM node as a child

Yes. You can just create a DOM node and it will be later injected into the DOM tree:

```jsx
function Component({class: className}) {
    let {node} = getContext();
    if (!node) {
        node = document.createElement('div');
    }
    node.className = className;
    return node;
}

sync(document.body, (
    <body>
        <Component class="native" />
    </body>
));
```

Other possible spec child types are:
- An object where `type` is a string (will create a DOM element).
- An object where `type` is a function (will invoke a component).
- An array or an object with `type` equal to Array constructor.
- A string (will create a text node).
- An inline function that returns a spec, like `({parent, node}) => <div />`.
- `null` (will leave a placeholder for future nodes).

## Animation plug-in

There is a built-in animation plug-in,
which makes it possible to schedule attributes' animations.
```jsx
import {m} from 'malevic';
import {render} from 'malevic/dom';
import {withAnimation, animate} from 'malevic/animation';

const Chart = withAnimation(({width, height}) => (
    <svg width={width} height={height}>
        <circle
            r={5}
            fill="red"
            cx={animate(90, {duration: 1000})}
            cy={animate(10, {duration: 1000})}
        />
        <path
            fill="none"
            stroke="blue"
            stroke-width={1}
            d={animate('M10,90 Q50,10 90,90')
              .initial('M10,10 Q50,90 90,10')}
        />
    </svg>
));

render(document.body, (
    <Chart width={200} height={150} />
));
```

It is possible to animate separate style properties:
```jsx
const Tooltip = withAnimation(({text, color, isVisible, x, y}) => {
    return (
        <div
            class={['tooltip', {'visible': isVisible}]}
            style={{
                'transform': animate(`translate(${x}px, ${y}px)`),
                'background-color': animate(color)
                    .interpolate(d3.interpolateRgb)
            }}
        ></div>
    );
});
```

A built-in interpolator can interpolate between numbers and strings containing numbers with floating points.
For other cases (e.g. colors) please use custom interpolators:
```jsx
<rect
    fill={animate([255, 255, 0], {duration: 2000})
        .initial([255, 0, 0])
        .interpolate((a, b) => (t) => {
            const mix = (x, y) => Math.round(x * (1 - t) + y * t);
            const channels = [
                mix(a[0], b[0]),
                mix(a[1], b[1]),
                mix(a[2], b[2])
            ];
            return `rgb(${channels.join(', ')})`;
        })}
/>
```

`initial()` method set's the initial value to a newly attached element,
from which it will start animating.
If the initial value was not provided,
the animation will start from the last value
or the final value will be used statically.

It is possible to add multiple keyframes:
```jsx
<polyline
    points={animate()
        .from([[0, 0], [10, 10]])
        .to([[20, 20], [40, 40]], {duration: 100, easing: 'linear'})
        .to([[50, 50], [40, 40]], {delay: 100, duration: 100})
        .to([[10, 10], [10, 10]], {easing: (t) => t * t})
        .output((points) => points.map(([x, y]) => `${x}, ${y}`).join(' '))}
/>
```

Sometimes it is easier to manipulate raw values rather than strings.
`output()` method could be used to convert data into an attribute or a CSS value.

## State plug-in	

State plug-in lets re-render a subtree in response for an interaction:	
```jsx	
import {m} from 'malevic';	
import {withState, useState} from 'malevic/state';	

export const Stateful = withState(({items}) => {
    const {state, setState} = useState({isExpanded: false});
    return (	
        <div>	
            <button onclick={() => setState({isExpanded: !state.isExpanded})}>	
                Expand	
            </button>	
            <ul class={{'expanded': state.isExpanded}}>	
                {items.map((text) => <li>{text}</li>)}	
            </ul>	
        </div>	
    );	
});
```

An initial state should be passed to `useState` function.
`setState` should not be called inside a component,
only in event handlers or async callbacks.

State plug-in is a shorthand for manipulating `context.store` property and `context.refresh()` method.

`context.store` is an object that is transferred between matched virtual nodes.
Any values can be stored there and used when the next component unboxing happens.

`context.getStore(defaults)` returns a store
and assigns it's default values.

`context.refresh()` function refreshes a part of the virtual DOM.
It should not be called during the component's unboxing.

```jsx
import {m} from 'malevic';	
import {getContext} from 'malevic/dom';	

function Stateful(({items}) {
    const context = getContext();
    const store = context.getStore({isExpanded: false});
    return (	
        <div>	
            <button onclick={() => {
                store.isExpanded = !store.isExpanded;
                context.refresh();
            }}>	
                Expand	
            </button>	
            <ul class={{'expanded': store.isExpanded}}>	
                {items.map((text) => <li>{text}</li>)}	
            </ul>	
        </div>	
    );	
});
```

## Forms plug-in

Forms plug-in makes form elements work in a reactive manner:
```jsx
import {m} from 'malevic';
import {withForms} from 'malevic/forms';

const Form = withForms(({checked, text, num, onCheckChange, onTextChange, onNumChange}) => {
    return (
        <form onsubmit={(e) => e.preventDefault()}>
            <input
                type="checkbox"
                checked={checked}
                onchange={(e) => onCheckChange(e.target.checked)}
            />
            <input
                type="number"
                value={num}
                readonly={!checked}
                onchange={(e) => {
                    if (!isNaN(e.target.valueAsNumber)) {
                        onNumChange(e.target.valueAsNumber);
                    }
                }}
                onkeypress={(e) => {
                    if (e.code === 'Enter' && !isNaN(e.target.valueAsNumber)) {
                        onNumChange(e.target.valueAsNumber);
                    }
                }}
            />
            <textarea oninput={(e) => onTextChange(e.target.value)}>
                {text}
            </textarea>
        </form>
    );
});
```

## Server-side rendering

Malevič.js can simply render inside existing HTML
without unnecessary DOM tree modifications.

```jsx
import {m} from 'malevic';
import {stringify} from 'malevic/string';
import {createServer} from 'http';
import App from './app';

createServer((request, response) => response.end(`<!DOCTYPE html>
<html>
<head></head>
${stringify(
    <body>
        <App state={{}} />
    </body>
, {indent: '  '})}
</html>`));
```

Sometimes a component is tied to DOM and cannot be converted to a string properly.
`isStringifying` function comes for rescue:

```jsx
import {m} from 'malevic';
import {getContext} from 'malevic/dom';
import {isStringifying} from 'malevic/string';

function Component() {
    if (isStringifying()) {
        return <div class="target" />;
    }

    const {parent} = getContext();
    const rect = parent.getBoundingClientRect();
    return <div
        class="target"
        style={{width: `${rect.width}px`}}
    />
}
```

## Canvas API

There is API for declaring hierarchical structures and drawing them on HTML5 Canvas.
```jsx
import {m} from 'malevic';
import {draw, getContext} from 'malevic/canvas';
import {render} from 'malevic/dom';

function Rect({width, height, fill}) {
    const context = getContext();
    context.fillStyle = fill;
    context.fillRect(0, 0, width, height);
}

function Transform({translate: {x, y}}, ...children) {
    const context = getContext();

    // Get previous transform value
    const prevTransform = context.getTransform();

    // Set new transform value
    context.translate(x, y);

    return [
        // Return children to draw
        children,

        // Restore transform value
        () => context.setTransform(prevTransform),
    ];
}

const canvas = render(
    document.body,
    <canvas width={1024} height={768} />
).firstElementChild;

draw(
    canvas.getContext('2d'),
    <Transform translate={{x: 32, y: 32}}>
        <Rect width={256} height={256} fill="black" />
    </Transform>
);
```

`getContext()` function returns the current rendering context.
Note that it is possible to use components' specifications,
as well as arrays and `(context) => ...` functions as child nodes.

## Custom plug-ins

There is API for adding custom logic
and making things more complex.
- `Plugins.add(Component, plugin)` method extends plugins list.
- If plugin returns `null` or `undefined` the next plugin (added earlier) will be used.

Extendable plug-ins:
- `dom.createElement` creates a DOM element.
- `dom.setAttribute` sets a DOM element's attribute.
- `string.isVoidTag` determines if HTML tag is void (empty) and cannot have a closing tag.
- `string.skipAttribute` determines whether the attribute should be skipped.
- `string.stringifyAttribute` converts the attribute value to a string.
**To prevent XSS attacks always use `escapeHTML` function**.

```javascript
import {plugins, sync} from 'malevic/dom';

const Component = () => <div/>;

const map = new WeakMap();

plugins.setAttribute
    .add(Component, ({element, attr, value, prev}) => {
        if (attr === 'data' && value !== prev) {
            map.set(element, value);
            return true;
        }
        return null;
    });

const div = sync(
    document.createElement('div'),
    <Component data={5} />
);

map.get(div) === 5;
```

## Breaking changes since version 0.18

- Element lifecycle methods were renamed to `oncreate`, `onupdate` and `onremove`.
`onrender` method was added (called when element was created or updated).
- Component lifecycle methods were renamed to `onCreate`, `onUpdate` and `onRemove`.
`onRender` method was added (called when component was created or updated).

## Breaking changes since version 0.12

Everything was broken up:
- Built-in ability to read previous props and store state.
- Parent and target DOM nodes can be retrieved using `getContext()` function.
- Lifecycle methods were renamed from `didmount`, `didupdate` and `willunmount`
to `attached`, `updated` and `detached` (called after DOM node removal).
- Components can return arrays.
- `native` attribute was removed, just use a DOM node as a child.
- Added ability to leave a component without changes.
- Limited plug-ins scope.
- Animation `.duration()` and `.easing()` methods were deleted,
values should be passed to `animate(value, {duration, easing, delay})` function
or `.to(value, {duration, easing, delay})` method.
- Multiple animation keyframes could be added.
