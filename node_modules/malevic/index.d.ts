declare namespace Malevic {
    /**
     * Specification for a DOM element
     */
    interface NodeSpec {
        /**
         * Tag name.
         */
        type: string;
        /**
         * Attributes and event listeners.
         */
        props: NodeAttrs;
        /**
         * Child specifications.
         */
        children: RecursiveArray<Child>;
    }

    /**
     * Specification for a component virtual node
     */
    interface ComponentSpec<TProps = any, TResult = any> {
        /**
         * A component function.
         */
        type: Component<TProps, TResult>;
        /**
         * Properties of a component.
         */
        props: TProps & {key?: any};
        /**
         * Child specifications.
         */
        children: RecursiveArray<Child>;
    }

    /**
     * Specification for a DOM element or a component.
     */
    type Spec = NodeSpec | ComponentSpec;

    /**
     * Component function.
     */
    type Component<TProps = any, TResult = Child> = (
        props: TProps & {key?: any},
        ...children: RecursiveArray<Child>
    ) => TResult | RecursiveArray<TResult> | any;

    /**
     * A function that returns a spec.
     */
    type InlineFunction<TContext = any, TResult = any> = (
        context: TContext,
    ) => TResult;

    /**
     * Possible specification child type.
     */
    type Child = Spec | string | Node | InlineFunction | null;

    interface DOMEventListener<
        TEvent = Event,
        TElement = Element,
        TTarget = Element,
    > {
        (
            this: TElement,
            e: TEvent & {target: TTarget; currentTarget: TElement},
        ): void;
    }

    /**
     * Attributes and event listeners of a DOM element.
     */
    interface NodeAttrs<TElement = Element, TTarget = Element> {
        key?: any;
        class?:
            | string
            | {[cls: string]: any}
            | (string | {[cls: string]: any})[];
        style?: string | {[prop: string]: any};
        /**
         * Is invoked when DOM node was created or inserted into DOM.
         */
        oncreate?: (el: TElement) => void;
        /**
         * Is invoked when DOM node and all descendants was updated.
         */
        onupdate?: (el: TElement) => void;
        /**
         * Is invoked when DOM node was created or updated.
         */
        onrender?: (el: TElement) => void;
        /**
         * Is invoked when DOM node was removed.
         */
        onremove?: (el: TElement) => void;
        [attr: string]: any | DOMEventListener;

        onclick?: DOMEventListener<MouseEvent, TElement, TTarget>;
        ondblclick?: DOMEventListener<MouseEvent, TElement, TTarget>;
        oncontextmenu?: DOMEventListener<MouseEvent, TElement, TTarget>;
        onmousedown?: DOMEventListener<MouseEvent, TElement, TTarget>;
        onmousemove?: DOMEventListener<MouseEvent, TElement, TTarget>;
        onmouseenter?: DOMEventListener<MouseEvent, TElement, TTarget>;
        onmouseleave?: DOMEventListener<MouseEvent, TElement, TTarget>;
        onmouseup?: DOMEventListener<MouseEvent, TElement, TTarget>;
        ontouchstart?: DOMEventListener<TouchEvent, TElement, TTarget>;
        ontouchmove?: DOMEventListener<TouchEvent, TElement, TTarget>;
        ontouchend?: DOMEventListener<TouchEvent, TElement, TTarget>;
        onkeydown?: DOMEventListener<KeyboardEvent, TElement, TTarget>;
        onkeyup?: DOMEventListener<KeyboardEvent, TElement, TTarget>;
        onkeypress?: DOMEventListener<KeyboardEvent, TElement, TTarget>;
        onscroll?: DOMEventListener<Event, TElement, TTarget>;
        onwheel?: DOMEventListener<WheelEvent, TElement, TTarget>;
    }

    type Plugin<TProps, TResult = any> = (props: TProps) => TResult;

    interface PluginsAPI<TProps, TResult = any> {
        add(
            type: Component,
            plugin: Plugin<TProps, TResult>,
        ): PluginsAPI<TProps, TResult>;
    }

    interface RecursiveArray<T> extends Array<T | RecursiveArray<T>> {}

    /**
     * Creates a DOM node specification.
     * @param tag Tag name.
     * @param attrs Attributes and event listeners.
     * @param children Child specifications.
     */
    function m(
        tag: string,
        attrs: NodeAttrs,
        ...children: RecursiveArray<Child>
    ): NodeSpec;
    /**
     * Creates a component specification.
     * @param component Component function.
     * @param props Properties of a component.
     * @param children Child specifications.
     */
    function m<TProps>(
        component: Component<TProps>,
        props: TProps & {key?: any},
        ...children: RecursiveArray<Child>
    ): ComponentSpec<TProps>;

    namespace Animation {
        interface AnimationDeclaration<TValue = any, TResult = any> {
            /**
             * Sets the value to start animation from.
             * @param from Value to start animation from.
             */
            from(from: TValue): this;
            /**
             * Adds a new keyframe.
             * @param to Keyframe value.
             * @param timing Transition timing parameters.
             */
            to(to: TValue, timing?: Partial<TimingSpec>): this;
            /**
             * Sets the initial value, that will be to start
             * animation from if there were no previous value.
             * @param from Initial value.
             */
            initial(from: TValue): this;
            /**
             * Sets a function, that interpolates between
             * start and end transition values.
             * @param interpolate Interpolator function.
             */
            interpolate(interpolate: Interpolator<any>): this;
            /**
             * Sets a function that transforms interpolated value
             * into an attribute or CSS value.
             * @param transformer A transformer function.
             */
            output(transformer: (value: TValue) => TResult): this;
            /**
             * Sets a function to be called when animation ends.
             * @param callback Callback function.
             */
            done(callback: () => void): this;
        }

        interface Interpolator<T> {
            (from: T, to: T): (t: number) => T;
        }

        interface TimingSpec {
            /**
             * Delay (milliseconds).
             */
            delay: number;
            /**
             * Duration (milliseconds).
             */
            duration: number;
            /**
             * Easing function name or custom function.
             */
            easing:
                | 'linear'
                | 'ease'
                | 'ease-in'
                | 'ease-out'
                | 'ease-in-out'
                | ((t: number) => number);
        }

        /**
         * Creates a new animation declaration.
         * @param to End transition value.
         * @param timing Animation timing parameters.
         */
        function animate(
            to?: any,
            timing?: Partial<TimingSpec>,
        ): AnimationDeclaration;

        /**
         * Makes component's DOM nodes to be animatable.
         * @param type Component function.
         */
        function withAnimation<TComponent extends Component>(
            type: TComponent,
        ): TComponent;
    }

    namespace Canvas {
        /**
         * Draws a spec on canvas.
         * @param context Canvas rendering context.
         * @param spec Component spec to draw on canvas.
         */
        function draw<TContext extends RenderingContext>(
            context: TContext,
            spec: Child | RecursiveArray<Child>,
        ): void;

        /**
         * Returns component context.
         */
        function getContext<
            TContext extends RenderingContext = CanvasRenderingContext2D,
        >(): TContext;

        type RenderingContext =
            | CanvasRenderingContext2D
            | OffscreenCanvasRenderingContext2D
            | ImageBitmapRenderingContext
            | WebGLRenderingContext
            | WebGL2RenderingContext;
    }

    namespace DOM {
        /**
         * Creates or updates child DOM nodes of an element.
         * @param element Target element.
         * @param spec Child specification or multiple specifications.
         */
        function render<TElement extends Element | Document | DocumentFragment>(
            element: TElement,
            spec: Child | Child[] | RecursiveArray<Child>,
        ): TElement;

        /**
         * Synchronizes DOM element with specification.
         * @param element Target DOM element.
         * @param spec Specification.
         */
        function sync<TElement extends Element>(
            element: TElement,
            spec: Spec,
        ): TElement;
        /**
         * Sets text node content.
         * @param node Target text node.
         * @param spec Specification or string.
         */
        function sync(node: Text, spec: Spec | string): Text;

        /**
         * Destroys virtual DOM assigned to a DOM node.
         * @param node Target DOM node.
         */
        function teardown(node: Element | Text): void;

        interface ComponentContext {
            /**
             * Specification of a component.
             */
            spec: Spec;
            /**
             * Previous specification of a component.
             */
            prev: Spec;
            /**
             * Store of a component.
             * Used to save values between re-renders.
             */
            store: any;
            /**
             * Returns a rendered DOM node.
             */
            node: Node;
            /**
             * Returns a list of rendered DOM siblings.
             */
            nodes: Node[];
            /**
             * Parent DOM node.
             */
            parent: Element;
            /**
             * Sets a callback, that will be invoked when
             * DOM node and all descendants will be created.
             * @param fn Event listener.
             */
            onCreate(fn: (node: Node) => void): void;
            /**
             * Sets a callback, that will be invoked when
             * DOM node and all descendants will be updated.
             * @param fn Event listener.
             */
            onUpdate(fn: (node: Node) => void): void;
            /**
             * Sets a callback, that will be invoked when
             * DOM node and all descendants will be created or updated.
             * @param fn Event listener.
             */
            onRender(fn: (node: Node) => void): void;
            /**
             * Sets a callback, that will be invoked
             * when DOM node will be removed.
             * @param fn Event listener.
             */
            onRemove(fn: (node: Node) => void): void;
            /**
             * Returns component's store.
             * @param defaults Default store values.
             */
            getStore<T>(defaults?: T): T;
            /**
             * Refreshes the component subtree.
             */
            refresh(): void;
            /**
             * Returns a special key, that stops
             * updating the component subtree.
             */
            leave(): any;
        }

        interface InlineFunctionContext {
            /**
             * Parent DOM element.
             */
            parent: Element;
            /**
             * Returns a rendered DOM node.
             */
            node: Node;
            /**
             * Returns rendered DOM nodes.
             */
            nodes: Node[];
        }

        /**
         * Returns a context of a component being unboxed.
         */
        function getContext(): ComponentContext;

        /**
         * Creates a component for convenient use in VanillaJS.
         * @param fn Component function
         */
        function component<TProps = any, TResult = any>(
            fn: (
                context: ComponentContext,
                props: TProps,
                ...children: RecursiveArray<Child>
            ) => TResult,
        ): Component<TProps, TResult>;

        interface TagFunction {
            (attrs: NodeAttrs, ...children: RecursiveArray<Child>): NodeSpec;
        }
        interface TagFunction {
            (...children: RecursiveArray<Child>): NodeSpec;
        }

        /**
         * Creates a shorthand for `m(tag, attrs, ...children)`
         * for use with VanillaJS.
         * The resulting function will generate DOM node specifications
         * for the tag name specified.
         * `attrs` argument can be omitted.
         */
        function tag(tag: string): TagFunction;

        /**
         * By invoking the properties of this object,
         * helper functions for generating DOM node specifications
         * for corresponding tag names are created.
         * This should be convenient for use in VanillaJS.
         */
        const tags: {[tag: string]: TagFunction};

        interface PluginCreateElementProps {
            /**
             * DOM element specification.
             */
            spec: NodeSpec;
            /**
             * Parent DOM element.
             */
            parent: Element;
        }

        interface PluginSetAttributeProps {
            /**
             * DOM element.
             */
            element: Element;
            /**
             * Attribute name.
             */
            attr: string;
            /**
             * Value.
             */
            value: any;
            /**
             * Previous value.
             */
            prev: any;
        }

        const plugins: {
            /**
             * Plugins for creating DOM elements.
             */
            createElement: PluginsAPI<PluginCreateElementProps>;
            /**
             * Plugins for setting DOM attribute value.
             */
            setAttribute: PluginsAPI<PluginSetAttributeProps>;
        };

        /**
         * Creates a specification from a DOM element.
         */
        function specFromNode(node: Node): NodeSpec;
    }

    namespace Forms {
        /**
         * Makes component's input fields react on value attribute.
         * @param type Component function.
         */
        function withForms<TComponent extends Component>(
            type: TComponent,
        ): TComponent;
    }

    namespace State {
        /**
         * Returns component's state and a function
         * for updating state.
         * @param initialState Initial state of a component.
         */
        function useState<TState extends {[prop: string]: any}>(
            initialState: TState,
        ): {
            /**
             * Component's state.
             */
            state: TState;
            /**
             * Sets state and refreshes the component.
             */
            setState: (newState: Partial<TState>) => void;
        };

        /**
         * Provides an API for reacting on state changes.
         * @param type Component function.
         */
        function withState<TComponent extends Component>(
            type: TComponent,
        ): TComponent;
    }

    namespace String {
        /**
         * Converts specification into a string.
         * @param spec DOM element or component specification.
         * @param options Stringifying options.
         */
        function stringify(
            spec: Spec,
            options?: Partial<StringifyOptions>,
        ): string;

        /**
         * Returns `true` if component
         * is being converted to string.
         */
        function isStringifying(): boolean;

        interface StringifyOptions {
            /**
             * Characters used for indentation.
             */
            indent: string;
            /**
             * Component's depth
             * (count of initial indents)
             */
            depth: number;
            /**
             * Indicates whether empty elements should
             * use XML self closing tags.
             */
            xmlSelfClosing: boolean;
        }

        interface PluginStringifyAttributeProps {
            /**
             * Attribute name.
             */
            attr: string;
            /**
             * Attribute value.
             */
            value: any;
        }

        interface PluginSkipAttributeProps {
            /**
             * Attribute name.
             */
            attr: string;
            /**
             * Attribute value.
             */
            value: any;
        }

        const plugins: {
            /**
             * Plugins for converting attribute
             * value to string.
             */
            stringifyAttribute: PluginsAPI<
                PluginStringifyAttributeProps,
                string
            >;
            /**
             * Plugins for skipping attributes.
             */
            skipAttribute: PluginsAPI<PluginSkipAttributeProps, boolean>;
            /**
             * Plugins for determining if DOM element is void (empty).
             */
            isVoidTag: PluginsAPI<string, boolean>;
        };

        /**
         * Converts string into a value which is safe
         * to insert into HTML document.
         * @param s Unsafe string value.
         */
        function escapeHTML(s: string): string;
    }
}

declare module 'malevic' {
    export = Malevic;
}

declare module 'malevic/animation' {
    export = Malevic.Animation;
}

declare module 'malevic/dom' {
    export = Malevic.DOM;
}

declare module 'malevic/forms' {
    export = Malevic.Forms;
}

declare module 'malevic/state' {
    export = Malevic.State;
}

declare module 'malevic/string' {
    export = Malevic.String;
}

declare namespace JSX {
    interface IntrinsicElements {
        [tag: string]: Malevic.NodeAttrs;
        input: Malevic.NodeAttrs<HTMLInputElement, HTMLInputElement> & {
            value?: any;
            onchange?: Malevic.DOMEventListener<
                Event,
                HTMLInputElement,
                HTMLInputElement
            >;
            oninput?: Malevic.DOMEventListener<
                Event,
                HTMLInputElement,
                HTMLInputElement
            >;
        };
        textarea: Malevic.NodeAttrs<
            HTMLTextAreaElement,
            HTMLTextAreaElement
        > & {
            onchange?: Malevic.DOMEventListener<
                Event,
                HTMLTextAreaElement,
                HTMLTextAreaElement
            >;
            oninput?: Malevic.DOMEventListener<
                Event,
                HTMLTextAreaElement,
                HTMLTextAreaElement
            >;
        };
        form: Malevic.NodeAttrs<HTMLFormElement, HTMLFormElement> & {
            onsubmit?: Malevic.DOMEventListener<
                Event,
                HTMLFormElement,
                HTMLFormElement
            >;
        };
    }

    type Element = any;
}
